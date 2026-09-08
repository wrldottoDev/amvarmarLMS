"""Sube los archivos del sistema viejo al storage privado (Paso 5.3).

El migrador de datos (Paso 5.2) registró cada documento con la ruta que tenía en
el servidor viejo y lo dejó en `UPLOADING`: la fila existe, el archivo no. Este
script cierra ese hueco.

    python -m scripts.migrate_files --media-root /ruta/a/media --dry-run
    python -m scripts.migrate_files --media-root /ruta/a/media
    python -m scripts.migrate_files --media-root /ruta/a/media --verificar

Se puede cortar y reanudar: un documento que ya se subió tiene
`storage_provider = 's3'` y se salta. Son casi 10 GB, así que interrumpirlo es
más probable que raro.

Lo que NO hace:

- **No borra el original.** El servidor viejo sigue siendo la copia de
  referencia hasta que el cutover termine.
"""

import argparse
import asyncio
import hashlib
import mimetypes
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_engine, get_sessionmaker
from app.infrastructure.storage import s3
from app.modules.documents.service import marcar_requisito_subido
from app.modules.documents.validation import detectar_media_type, nombre_seguro

# Cuánto se lee de una vez al calcular el hash. Un archivo de 400 MB no debe
# entrar entero en memoria solo para sacarle el SHA-256.
_TROZO = 1024 * 1024


@dataclass
class Reporte:
    subidos: int = 0
    ya_estaban: int = 0
    faltantes: list[str] = field(default_factory=list)
    discrepancias: list[str] = field(default_factory=list)
    bytes_subidos: int = 0

    def imprimir(self, *, seco: bool) -> None:
        titulo = "SIMULACIÓN (no se subió nada)" if seco else "SUBIDA COMPLETA"
        print(f"\n{'=' * 62}\n{titulo}\n{'=' * 62}")
        print(f"  subidos:      {self.subidos}")
        print(f"  ya estaban:   {self.ya_estaban}")
        print(f"  volumen:      {self.bytes_subidos / (1024 * 1024):.1f} MB")
        print(f"  faltantes:    {len(self.faltantes)}")
        print(f"  discrepancias:{len(self.discrepancias)}")

        for titulo_lista, elementos in (
            ("Archivos que no existen en media/", self.faltantes),
            ("Metadatos que no coinciden con el legacy", self.discrepancias),
        ):
            if not elementos:
                continue
            print(f"\n{titulo_lista} ({len(elementos)}):")
            for linea in elementos[:30]:
                print(f"  - {linea}")
            if len(elementos) > 30:
                print(f"  ... y {len(elementos) - 30} más")


def sha256_y_tamano(ruta: Path) -> tuple[str, int]:
    """Hash y tamaño leyendo por trozos, sin cargar el archivo entero."""
    resumen = hashlib.sha256()
    total = 0
    with ruta.open("rb") as archivo:
        while trozo := archivo.read(_TROZO):
            resumen.update(trozo)
            total += len(trozo)
    return resumen.hexdigest(), total


def clave_de_storage(company_id: UUID, document_id: UUID, nombre: str) -> str:
    """Misma forma que la de una subida normal.

    Que los archivos migrados vivan bajo otra convención obligaría a que todo lo
    que lee del bucket conozca dos formatos.
    """
    hoy = datetime.now(UTC)
    return f"{company_id}/{hoy:%Y/%m}/{document_id}-{secrets.token_hex(8)}-{nombre_seguro(nombre)}"


async def _pendientes(session: AsyncSession) -> list[dict[str, object]]:
    filas = (
        await session.execute(
            text("""
                SELECT id, company_id, storage_key AS ruta_legacy, original_name,
                       media_type, size_bytes
                FROM documents
                WHERE storage_provider = 'legacy' AND deleted_at IS NULL
                ORDER BY created_at
            """)
        )
    ).all()
    return [dict(f._mapping) for f in filas]


async def subir(
    session: AsyncSession, *, media_root: Path, seco: bool, limite: int | None = None
) -> Reporte:
    reporte = Reporte()
    documentos = await _pendientes(session)

    if limite:
        documentos = documentos[:limite]

    # Se resuelve una sola vez, fuera del bucle: es E/S de disco y no cambia.
    raiz = media_root.resolve()

    total = len(documentos)
    for indice, documento in enumerate(documentos, start=1):
        ruta_legacy = str(documento["ruta_legacy"])
        # `resolve()` y la comprobación de prefijo evitan que una ruta con `..`
        # guardada en la base haga leer fuera de `media/`.
        ruta = (media_root / ruta_legacy).resolve()
        if not ruta.is_relative_to(raiz):
            reporte.faltantes.append(f"{ruta_legacy} (ruta fuera de media/)")
            continue

        if not ruta.is_file():
            reporte.faltantes.append(ruta_legacy)
            continue

        hash_real, tamano_real = sha256_y_tamano(ruta)

        # El legacy guardaba el tamaño por su cuenta; si no coincide con el
        # archivo, alguien lo reemplazó o el registro quedó viejo. Se anota y se
        # sube igual: el archivo es la verdad, el metadato no.
        tamano_legacy = documento["size_bytes"]
        # El migrador de datos pone 1 cuando el legacy no guardaba el tamaño;
        # comparar contra ese relleno daría una discrepancia en todas las filas.
        if isinstance(tamano_legacy, int) and tamano_legacy not in (tamano_real, 1):
            reporte.discrepancias.append(
                f"{ruta_legacy}: el legacy decía {tamano_legacy} B y el archivo tiene {tamano_real} B"
            )

        with ruta.open("rb") as archivo:
            cabecera = archivo.read(8192)
        media_type = detectar_media_type(cabecera) or (
            mimetypes.guess_type(ruta.name)[0] or "application/octet-stream"
        )

        clave = clave_de_storage(
            UUID(str(documento["company_id"])),
            UUID(str(documento["id"])),
            str(documento["original_name"]),
        )

        if not seco:
            await s3.subir_archivo(str(ruta), clave, media_type=media_type)
            await session.execute(
                text("""
                    UPDATE documents
                    SET storage_provider = 's3', storage_key = :clave,
                        sha256 = :hash, size_bytes = :tamano, media_type = :tipo,
                        safe_name = :seguro,
                        upload_status = 'READY'
                    WHERE id = :id
                """),
                {
                    "clave": clave,
                    "hash": hash_real,
                    "tamano": tamano_real,
                    "tipo": media_type,
                    "seguro": nombre_seguro(str(documento["original_name"])),
                    "id": documento["id"],
                },
            )
            await marcar_requisito_subido(session, UUID(str(documento["id"])))
            # Commit por documento: con 10 GB por delante, una sola transacción
            # gigante pierde todo el avance si el proceso se corta.
            await session.commit()

        reporte.subidos += 1
        reporte.bytes_subidos += tamano_real

        if indice % 25 == 0 or indice == total:
            print(f"  {indice}/{total} · {reporte.bytes_subidos / (1024 * 1024):.0f} MB")

    return reporte


async def verificar(session: AsyncSession) -> Reporte:
    """Comprueba uno por uno que el objeto está y que el hash coincide.

    No es un muestreo: un archivo que no llegó no se nota hasta que alguien lo
    necesita, y para entonces el servidor viejo puede estar apagado. Se relee
    desde el storage y se recalcula el hash — comparar contra lo que la base
    dice de sí misma no probaría nada.
    """
    reporte = Reporte()
    filas = (
        await session.execute(
            text("""
                SELECT id, storage_key, sha256, size_bytes, original_name
                FROM documents
                WHERE storage_provider = 's3' AND deleted_at IS NULL
                ORDER BY created_at
            """)
        )
    ).all()

    total = len(filas)
    for indice, fila in enumerate(filas, start=1):
        try:
            objeto = await s3.describir_objeto(fila.storage_key)
        except Exception:
            reporte.faltantes.append(f"{fila.original_name}: no está en el storage")
            continue

        if objeto.size_bytes != fila.size_bytes:
            reporte.discrepancias.append(
                f"{fila.original_name}: la base dice {fila.size_bytes} B "
                f"y el storage tiene {objeto.size_bytes} B"
            )
            continue

        resumen = hashlib.sha256()
        bytes_leidos = 0
        async for chunk in s3.iterar_chunks(fila.storage_key):
            resumen.update(chunk)
            bytes_leidos += len(chunk)
        if bytes_leidos != fila.size_bytes:
            reporte.discrepancias.append(
                f"{fila.original_name}: se leyeron {bytes_leidos} B, "
                f"pero la base registra {fila.size_bytes} B"
            )
            continue
        if resumen.hexdigest() != fila.sha256:
            reporte.discrepancias.append(
                f"{fila.original_name}: el hash del storage no coincide con el guardado"
            )
            continue

        reporte.subidos += 1
        reporte.bytes_subidos += objeto.size_bytes

        if indice % 25 == 0 or indice == total:
            print(f"  verificados {indice}/{total}")

    return reporte


async def principal() -> None:
    parser = argparse.ArgumentParser(
        description="Sube los archivos del sistema viejo al storage privado."
    )
    parser.add_argument(
        "--media-root",
        type=Path,
        required=True,
        help="Carpeta `media/` del servidor viejo, ya copiada a esta máquina.",
    )
    parser.add_argument("--dry-run", action="store_true", help="No sube nada; solo reporta.")
    parser.add_argument(
        "--verificar",
        action="store_true",
        help="No sube: relee lo ya subido y recalcula su hash.",
    )
    parser.add_argument("--limite", type=int, help="Procesa solo los primeros N (para probar).")
    argumentos = parser.parse_args()

    if not argumentos.verificar and not argumentos.media_root.is_dir():
        raise SystemExit(f"No existe la carpeta {argumentos.media_root}")

    async with get_sessionmaker()() as session:
        if argumentos.verificar:
            reporte = await verificar(session)
            print(f"\n{'=' * 62}\nVERIFICACIÓN\n{'=' * 62}")
            print(f"  correctos:    {reporte.subidos}")
            print(f"  faltantes:    {len(reporte.faltantes)}")
            print(f"  discrepancias:{len(reporte.discrepancias)}")
            for linea in (reporte.faltantes + reporte.discrepancias)[:30]:
                print(f"  - {linea}")
        else:
            reporte = await subir(
                session,
                media_root=argumentos.media_root,
                seco=argumentos.dry_run,
                limite=argumentos.limite,
            )
            reporte.imprimir(seco=argumentos.dry_run)

    await get_engine().dispose()

    if reporte.faltantes or reporte.discrepancias:
        # El gate del paso es cero faltantes y cero discrepancias sin explicar.
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(principal())

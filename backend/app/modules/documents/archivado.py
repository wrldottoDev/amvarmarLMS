"""Recompresión de documentos de cargas archivadas (ADR-0007).

Corre DESPUÉS de que `shipments.service.archivar_pendientes` marca
`archived_at` — un fallo acá nunca debe impedirle a una carga salir del
flujo operativo a tiempo, por eso es un worker aparte.

Dos caminos según `documents.is_digitally_signed`:

- **Sin firma** (la mayoría): se recomprime el CONTENIDO — Ghostscript para
  PDF, Pillow para imagen — y la versión comprimida REEMPLAZA al original en
  storage. Es la única situación del proyecto donde un original se
  reemplaza, justificada explícitamente por ahorro de espacio en archivo
  histórico, no por optimización de visualización.
- **PDF firmado digitalmente**: nunca se toca el contenido — modificar un
  solo byte invalida la firma. Se aplica compresión de CONTENEDOR (gzip) sin
  pérdida: el objeto en storage queda gzip con metadata `ContentEncoding:
  gzip`, que el cliente HTTP descomprime solo al descargar — el archivo que
  llega al usuario es bit-idéntico al original firmado, y el endpoint de
  descarga (`documents.service.preparar_descarga`) no necesita ningún
  cambio.

DOCX/XLSX/CSV/TXT no tienen una herramienta de recompresión de contenido
con sentido instalada — se marcan igual (`archived_compressed_at`, para que
el barrido no los reintente para siempre) sin cambiar el archivo. Un 0% de
reducción es un resultado honesto, no un error ni algo para inventar.
"""

from __future__ import annotations

import gzip
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from PIL import Image
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.storage import s3
from app.modules.audit.service import registrar

_FORMATOS_IMAGEN: dict[str, str] = {
    "image/jpeg": "JPEG",
    "image/png": "PNG",
    "image/webp": "WEBP",
}
# HEIC no tiene soporte nativo en Pillow sin el plugin `pillow-heif`, que no
# está instalado — se deja sin recomprimir en vez de fingir que se procesó.

_TAMANO_CHUNK = 8 * 1024 * 1024


@dataclass(frozen=True)
class ResultadoRecompresion:
    document_id: UUID
    size_bytes_antes: int
    size_bytes_despues: int


async def documentos_pendientes(session: AsyncSession, *, limite: int = 100) -> list[Any]:
    """Documentos de cargas YA archivadas que todavía no se recomprimieron."""
    return list(
        (
            await session.execute(
                text("""
                    SELECT d.id, d.company_id, d.storage_key, d.media_type, d.size_bytes,
                           d.is_digitally_signed, s.id AS shipment_id
                    FROM documents d
                    JOIN shipment_documents sd ON sd.document_id = d.id
                    JOIN shipments s ON s.id = sd.shipment_id
                    WHERE s.archived_at IS NOT NULL
                      AND d.archived_compressed_at IS NULL
                      AND d.upload_status = 'READY'
                      AND d.deleted_at IS NULL
                    ORDER BY s.archived_at
                    LIMIT :limite
                """),
                {"limite": limite},
            )
        ).all()
    )


async def _descargar_a(storage_key: str, destino: Path) -> None:
    with destino.open("wb") as salida:
        async for chunk in s3.iterar_chunks(storage_key):
            salida.write(chunk)


def _recomprimir_imagen(origen: Path, destino: Path, media_type: str) -> None:
    formato = _FORMATOS_IMAGEN[media_type]
    with Image.open(origen) as imagen:
        # `quality=40`: orientado a archivo histórico, no a uso diario
        # (ADR-0007) — más agresivo que cualquier optimización de
        # visualización. PNG ignora `quality`, pero `optimize=True` sí
        # aprieta la paleta/filtros.
        imagen.save(destino, format=formato, optimize=True, quality=40)


def _recomprimir_pdf(origen: Path, destino: Path) -> None:
    """`/screen` es el preset más agresivo de Ghostscript (72 dpi en
    imágenes internas) — a propósito: consulta esporádica, no operación
    diaria."""
    subprocess.run(  # noqa: S603 -- lista fija de argumentos; lo único variable son rutas propias
        [  # noqa: S607 -- "gs" es el binario del sistema (Dockerfile), no una entrada del usuario
            "gs",
            "-sDEVICE=pdfwrite",
            "-dCompatibilityLevel=1.4",
            "-dPDFSETTINGS=/screen",
            "-dNOPAUSE",
            "-dBATCH",
            "-dQUIET",
            f"-sOutputFile={destino}",
            str(origen),
        ],
        check=True,
        timeout=120,
    )


def _comprimir_contenedor(origen: Path, destino: Path) -> None:
    with origen.open("rb") as entrada, gzip.open(destino, "wb", compresslevel=9) as salida:
        while chunk := entrada.read(_TAMANO_CHUNK):
            salida.write(chunk)


async def recomprimir(session: AsyncSession, *, documento: Any) -> ResultadoRecompresion:
    size_antes = documento.size_bytes

    with tempfile.TemporaryDirectory(prefix="amvarmar-archivado-") as directorio_str:
        directorio = Path(directorio_str)
        origen = directorio / "origen"
        await _descargar_a(documento.storage_key, origen)

        if documento.is_digitally_signed:
            destino = directorio / "destino.gz"
            _comprimir_contenedor(origen, destino)
            await s3.subir_archivo(
                str(destino),
                documento.storage_key,
                media_type=documento.media_type,
                content_encoding="gzip",
            )
            size_despues = destino.stat().st_size
        elif documento.media_type in _FORMATOS_IMAGEN:
            destino = directorio / "destino"
            _recomprimir_imagen(origen, destino, documento.media_type)
            await s3.subir_archivo(
                str(destino), documento.storage_key, media_type=documento.media_type
            )
            size_despues = destino.stat().st_size
        elif documento.media_type == "application/pdf":
            destino = directorio / "destino.pdf"
            _recomprimir_pdf(origen, destino)
            await s3.subir_archivo(
                str(destino), documento.storage_key, media_type=documento.media_type
            )
            size_despues = destino.stat().st_size
        else:
            size_despues = size_antes

    await session.execute(
        text("""
            UPDATE documents
            SET original_size_bytes = :antes, size_bytes = :despues,
                archived_compressed_at = now()
            WHERE id = :id
        """),
        {"antes": size_antes, "despues": size_despues, "id": documento.id},
    )
    await registrar(
        session,
        action="document.archived_compressed",
        resource_type="document",
        resource_id=documento.id,
        company_id=documento.company_id,
        # actor_user_id=None: lo ejecutó el barrido, no una persona.
        after_data={
            "shipment_id": str(documento.shipment_id),
            "size_bytes_antes": size_antes,
            "size_bytes_despues": size_despues,
        },
    )

    return ResultadoRecompresion(
        document_id=documento.id, size_bytes_antes=size_antes, size_bytes_despues=size_despues
    )


async def recomprimir_pendientes(session: AsyncSession, *, limite: int = 100) -> int:
    pendientes = await documentos_pendientes(session, limite=limite)
    for documento in pendientes:
        await recomprimir(session, documento=documento)
    return len(pendientes)

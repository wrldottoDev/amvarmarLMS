"""Compara el sistema viejo contra el nuevo, tabla por tabla (Paso 5.4).

El ensayo general: se migra sobre una copia y se comprueba que todo llegó. No
sirve mirar solo los totales — que coincidan 241 con 241 no dice que sean las
mismas 241.

    python -m scripts.verificar_migracion
    python -m scripts.verificar_migracion --muestra 25

`--muestra` saca expedientes reales para revisar a mano con la operación de
AMVARMAR. Ese es el gate del paso, y no lo puede aprobar un script: alguien que
conoce el negocio tiene que mirar una carga concreta y decir si su estado, su
empresa y sus documentos son los correctos.
"""

import argparse
import asyncio
from dataclasses import dataclass, field
from typing import Any

import psycopg
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_engine, get_sessionmaker
from scripts.migrate_legacy import LEGACY_URL_POR_DEFECTO


@dataclass
class Comparacion:
    concepto: str
    origen: int
    destino: int
    nota: str = ""

    @property
    def cuadra(self) -> bool:
        return self.origen == self.destino


@dataclass
class Informe:
    comparaciones: list[Comparacion] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)

    @property
    def descuadres(self) -> list[Comparacion]:
        return [c for c in self.comparaciones if not c.cuadra]

    def imprimir(self) -> None:
        print(f"\n{'=' * 74}\nCONTEOS ORIGEN CONTRA DESTINO\n{'=' * 74}")
        print(f"{'concepto':<34}{'legacy':>10}{'nuevo':>10}{'':>4}  nota")
        print("-" * 74)
        for c in self.comparaciones:
            marca = "  " if c.cuadra else "!!"
            print(f"{c.concepto:<34}{c.origen:>10}{c.destino:>10}{marca:>4}  {c.nota}")

        if self.avisos:
            print(f"\nAvisos ({len(self.avisos)}):")
            for aviso in self.avisos:
                print(f"  - {aviso}")

        if self.descuadres:
            print(f"\n{len(self.descuadres)} descuadre(s). Cada uno necesita explicación:")
            for c in self.descuadres:
                print(f"  - {c.concepto}: legacy {c.origen}, nuevo {c.destino}")
        else:
            print("\nTodos los conteos cuadran.")


def _uno(legacy: psycopg.Connection, consulta: str) -> int:
    with legacy.cursor() as cur:
        cur.execute(consulta)
        fila = cur.fetchone()
        return int(fila[0]) if fila else 0


async def _uno_nuevo(session: AsyncSession, consulta: str) -> int:
    return int((await session.execute(text(consulta))).scalar_one())


async def comparar(legacy: psycopg.Connection, session: AsyncSession) -> Informe:
    informe = Informe()

    # Usuarios: los que no tenían correo no se migran como cuenta con acceso, y
    # el migrador crea además una cuenta de sistema. Los dos ajustes se
    # explican en la nota en vez de disimularse en el número.
    sin_correo = _uno(legacy, "SELECT count(*) FROM auth_user WHERE coalesce(trim(email),'') = ''")
    cuenta_sistema = await _uno_nuevo(
        session, "SELECT count(*) FROM users WHERE email = 'migracion@sistema.amvarmar.com'"
    )

    informe.comparaciones += [
        Comparacion(
            "usuarios",
            _uno(legacy, "SELECT count(*) FROM auth_user") - sin_correo,
            await _uno_nuevo(session, "SELECT count(*) FROM users") - cuenta_sistema,
            f"{sin_correo} sin correo no migrados; +{cuenta_sistema} cuenta de sistema",
        ),
        Comparacion(
            "empresas",
            _uno(legacy, "SELECT count(*) FROM core_company"),
            await _uno_nuevo(session, "SELECT count(*) FROM companies"),
        ),
        Comparacion(
            "cargas",
            _uno(legacy, "SELECT count(*) FROM core_warehouse"),
            await _uno_nuevo(session, "SELECT count(*) FROM shipments"),
        ),
        Comparacion(
            "despachos",
            _uno(legacy, "SELECT count(*) FROM core_dispatchrequest"),
            await _uno_nuevo(session, "SELECT count(*) FROM dispatch_requests"),
        ),
        Comparacion(
            "cargas dentro de despachos",
            _uno(legacy, "SELECT count(*) FROM core_dispatchrequestitem"),
            await _uno_nuevo(session, "SELECT count(*) FROM dispatch_request_shipments"),
        ),
        Comparacion(
            "documentos",
            _uno(legacy, "SELECT count(*) FROM core_warehousedocument")
            + _uno(legacy, "SELECT count(*) FROM core_warehouseinvoice")
            + _uno(legacy, "SELECT count(*) FROM core_dispatchbldocument")
            + _uno(
                legacy,
                "SELECT count(*) FROM core_warehouse "
                "WHERE uploaded_file IS NOT NULL AND uploaded_file <> ''",
            ),
            await _uno_nuevo(session, "SELECT count(*) FROM documents"),
            "adjuntos, facturas, BL y Warehouse Receipts",
        ),
        Comparacion(
            "despachos terrestres",
            _uno(
                legacy,
                "SELECT count(*) FROM core_dispatchrequest WHERE upper(trim(method)) = 'TERRESTRE'",
            ),
            await _uno_nuevo(
                session, "SELECT count(*) FROM dispatch_requests WHERE method = 'LAND'"
            ),
            "TERRESTRE debe conservarse como LAND",
        ),
        Comparacion(
            "adjuntos legacy sin clasificar",
            _uno(legacy, "SELECT count(*) FROM core_warehousedocument"),
            await _uno_nuevo(
                session,
                """
                SELECT count(*) FROM shipment_documents sd
                JOIN document_types dt ON dt.id = sd.document_type_id
                WHERE upper(dt.code) = 'LEGACY_UNCLASSIFIED'
                """,
            ),
            "ningún adjunto genérico se interpreta como factura",
        ),
    ]

    informe.comparaciones.append(
        Comparacion(
            "bultos",
            _uno(legacy, "SELECT count(*) FROM core_piecewarehouse"),
            await _uno_nuevo(session, "SELECT count(*) FROM shipment_packages"),
            "una fila por core_piecewarehouse; quantity se conserva",
        )
    )

    # Comprobaciones que no son conteos comparables sino invariantes del destino.
    for descripcion, consulta, esperado in (
        (
            "cargas sin ninguna referencia",
            """
            SELECT count(*) FROM shipments s
            WHERE NOT EXISTS (SELECT 1 FROM shipment_references r WHERE r.shipment_id = s.id)
            """,
            0,
        ),
        (
            "cargas sin empresa",
            "SELECT count(*) FROM shipments WHERE company_id IS NULL",
            0,
        ),
        (
            "documentos sin padre asociado",
            """
            SELECT count(*) FROM documents d
            WHERE NOT EXISTS (SELECT 1 FROM shipment_documents sd WHERE sd.document_id = d.id)
              AND NOT EXISTS (SELECT 1 FROM dispatch_documents dd WHERE dd.document_id = d.id)
            """,
            0,
        ),
        (
            "cargas sin piezas",
            """
            SELECT count(*) FROM shipments s
            WHERE s.deleted_at IS NULL
              AND NOT EXISTS (
                  SELECT 1 FROM shipment_packages p WHERE p.shipment_id = s.id
              )
            """,
            0,
        ),
        (
            "conteos de piezas inconsistentes",
            """
            SELECT count(*) FROM shipments s
            WHERE s.package_count <> COALESCE(
                (SELECT sum(p.quantity) FROM shipment_packages p WHERE p.shipment_id = s.id), 0
            )
            """,
            0,
        ),
        (
            "BL ligados a cargas",
            """
            SELECT count(*) FROM shipment_documents sd
            JOIN document_types dt ON dt.id = sd.document_type_id
            WHERE upper(dt.code) = 'BL'
            """,
            0,
        ),
        (
            "documentos ligados a ambos contextos",
            """
            SELECT count(*) FROM shipment_documents sd
            JOIN dispatch_documents dd ON dd.document_id = sd.document_id
            """,
            0,
        ),
        (
            "usuarios de cliente sin empresa",
            """
            SELECT count(*) FROM user_role_assignments a
            JOIN roles r ON r.id = a.role_id
            WHERE r.code LIKE 'CLIENT%' AND a.company_id IS NULL
            """,
            0,
        ),
        (
            "roles sin permisos",
            """
            SELECT count(*) FROM roles r
            WHERE NOT EXISTS (SELECT 1 FROM role_permissions p WHERE p.role_id = r.id)
            """,
            0,
        ),
    ):
        real = await _uno_nuevo(session, consulta)
        if real != esperado:
            informe.avisos.append(f"{descripcion}: {real} (se esperaba {esperado})")

    marcadas = await _uno_nuevo(
        session, "SELECT count(*) FROM shipments WHERE legacy_review_required"
    )
    total = await _uno_nuevo(session, "SELECT count(*) FROM shipments")
    if marcadas:
        porcentaje = 100 * marcadas / total if total else 0
        informe.avisos.append(
            f"{marcadas} cargas marcadas para revisión ({porcentaje:.0f}% del total). "
            "Hay que resolverlas con la operación antes de cerrar la Fase 5."
        )

    pendientes = await _uno_nuevo(
        session, "SELECT count(*) FROM documents WHERE storage_provider = 'legacy'"
    )
    if pendientes:
        informe.avisos.append(
            f"{pendientes} documentos sin subir al storage. Falta correr `migrate_files` (Paso 5.3)."
        )

    return informe


async def muestrear(session: AsyncSession, cantidad: int) -> list[dict[str, Any]]:
    """Expedientes reales para revisar a mano.

    Se toman de estados distintos a propósito: revisar veinte cargas
    despachadas no diría nada sobre las que quedaron marcadas para revisión.
    """
    filas = (
        await session.execute(
            text("""
                WITH numeradas AS (
                    SELECT s.id,
                           row_number() OVER (PARTITION BY s.current_status_code
                                              ORDER BY s.created_at DESC) AS puesto
                    FROM shipments s
                )
                SELECT s.shipment_number, s.current_status_code, s.legacy_status,
                       s.legacy_review_required, c.legal_name,
                       s.created_at::date AS creada,
                       (SELECT string_agg(r.reference_type || ':' || r.value, ' ')
                          FROM shipment_references r WHERE r.shipment_id = s.id) AS referencias,
                       (SELECT count(*) FROM shipment_packages p WHERE p.shipment_id = s.id)
                           AS bultos,
                       (SELECT count(*) FROM shipment_documents sd WHERE sd.shipment_id = s.id)
                           AS documentos,
                       (SELECT count(*) FROM shipment_events e WHERE e.shipment_id = s.id)
                           AS eventos
                FROM shipments s
                JOIN companies c ON c.id = s.company_id
                JOIN numeradas n ON n.id = s.id
                WHERE n.puesto <= :por_estado
                ORDER BY s.current_status_code, s.created_at DESC
                LIMIT :cantidad
            """),
            {"cantidad": cantidad, "por_estado": max(cantidad // 4, 3)},
        )
    ).all()
    return [dict(f._mapping) for f in filas]


def imprimir_muestra(expedientes: list[dict[str, Any]]) -> None:
    print(f"\n{'=' * 74}\nMUESTRA PARA REVISAR CON LA OPERACIÓN\n{'=' * 74}")
    print("Por cada uno, preguntar: ¿el estado es correcto?, ¿la empresa es la")
    print("correcta?, ¿están sus documentos?, ¿la línea de tiempo tiene sentido?\n")

    for e in expedientes:
        marca = "  [REVISAR]" if e["legacy_review_required"] else ""
        print(f"{e['shipment_number']}  {e['legal_name']}{marca}")
        print(f"    estado: {e['current_status_code']}  (legacy: {e['legacy_status']})")
        print(
            f"    creada: {e['creada']}  bultos: {e['bultos']}  "
            f"documentos: {e['documentos']}  eventos: {e['eventos']}"
        )
        print(f"    referencias: {e['referencias'] or 'ninguna'}")
        print()

    print(f"{len(expedientes)} expedientes. El gate del Paso 5.4 es que AMVARMAR")
    print("apruebe esta muestra POR ESCRITO. Ningún script puede darla por buena.")


async def principal() -> None:
    parser = argparse.ArgumentParser(description="Compara el legacy contra el sistema nuevo.")
    parser.add_argument("--legacy-url", default=LEGACY_URL_POR_DEFECTO)
    parser.add_argument(
        "--muestra", type=int, default=0, help="Cuántos expedientes sacar para revisión manual."
    )
    argumentos = parser.parse_args()

    with psycopg.connect(argumentos.legacy_url) as legacy:
        async with get_sessionmaker()() as session:
            informe = await comparar(legacy, session)
            expedientes = await muestrear(session, argumentos.muestra) if argumentos.muestra else []

    await get_engine().dispose()

    informe.imprimir()
    if expedientes:
        imprimir_muestra(expedientes)

    if informe.descuadres:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(principal())

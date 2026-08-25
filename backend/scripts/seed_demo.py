"""Datos de demostración para trastear el sistema en local.

NO es para producción y el script se niega a correr fuera de `local`: crea
usuarios con contraseñas conocidas, y eso en un entorno real es una puerta
abierta.

Siembra lo mínimo para que todas las pantallas tengan algo que mostrar: dos
empresas cliente con sus usuarios, personal de operaciones, cargas repartidas
por todos los estados, requisitos documentales abiertos, un despacho en curso y
notificaciones sin leer.

    python -m scripts.seed_demo

Es idempotente: correrlo de nuevo no duplica nada. Para empezar de cero:

    python -m scripts.seed_demo --limpiar
"""

import argparse
import asyncio
import random
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_engine, get_sessionmaker
from app.core.security.argon2 import hash_password
from app.modules.notifications import service as notificaciones
from app.modules.rbac.models import RoleCode, ScopeType
from app.modules.shipments.models import ShipmentStatus
from scripts.seed_document_types import sembrar as sembrar_documentos
from scripts.seed_rbac import sembrar as sembrar_rbac
from scripts.seed_shipment_statuses import sembrar as sembrar_estados

# Contraseña única para todas las cuentas de demo. Es pública a propósito: está
# acá escrita, así que nadie puede confundirla con una credencial real.
PASSWORD_DEMO = "Demo-AMVARMAR-2026"

# No usar `.local`, `.test` ni `.example`: son TLD reservados y el validador de
# correo de Pydantic los rechaza, así que el login fallaría con 422 antes de
# llegar a comprobar la contraseña.
DOMINIO = "demo.amvarmar.com"

CUENTAS = [
    ("admin@" + DOMINIO, "Sofía", "Ramírez", RoleCode.SUPER_ADMIN, ScopeType.GLOBAL, None),
    ("operaciones@" + DOMINIO, "Diego", "Mora", RoleCode.OPS_ADMIN, ScopeType.GLOBAL, None),
    ("agente@" + DOMINIO, "Karla", "Solís", RoleCode.OPS_AGENT, ScopeType.GLOBAL, None),
    (
        "cliente@" + DOMINIO,
        "Andrés",
        "Vargas",
        RoleCode.CLIENT_ADMIN,
        ScopeType.ORGANIZATION,
        "alfa",
    ),
    (
        "cliente2@" + DOMINIO,
        "Marta",
        "Chaves",
        RoleCode.CLIENT_USER,
        ScopeType.ORGANIZATION,
        "alfa",
    ),
    ("beta@" + DOMINIO, "Luis", "Herrera", RoleCode.CLIENT_ADMIN, ScopeType.ORGANIZATION, "beta"),
]

EMPRESAS = {
    "alfa": ("Importaciones Alfa S.A.", "3-101-100001"),
    "beta": ("Comercial Beta Ltda.", "3-101-100002"),
}

UBICACIONES = [
    ("US", "MIA", "Miami"),
    ("CR", "SJO", "San José"),
    ("CR", "LIO", "Limón"),
]

# Reparto por estado, para que los listados y el tablero no se vean vacíos ni
# todos iguales.
REPARTO = {
    ShipmentStatus.PRE_ALERT: 4,
    ShipmentStatus.IN_TRANSIT: 6,
    ShipmentStatus.RECEIVED: 3,
    ShipmentStatus.STORED: 9,
    ShipmentStatus.DISPATCHED: 5,
    ShipmentStatus.DELIVERED: 12,
}


# Qué hitos ya ocurrieron al llegar a cada estado. Una carga entregada pasó
# antes por recibida, almacenada y despachada, y sus fechas deben estar puestas
# o la línea de tiempo se ve incompleta.
_HITOS_ALCANZADOS: dict[str, frozenset[str]] = {
    ShipmentStatus.PRE_ALERT: frozenset(),
    ShipmentStatus.IN_TRANSIT: frozenset(),
    ShipmentStatus.RECEIVED: frozenset({"received"}),
    ShipmentStatus.STORED: frozenset({"received", "stored"}),
    ShipmentStatus.DISPATCHED: frozenset({"received", "stored", "dispatched"}),
    ShipmentStatus.DELIVERED: frozenset({"received", "stored", "dispatched", "delivered"}),
}


async def _empresa(session: AsyncSession, clave: str) -> UUID:
    nombre, cedula = EMPRESAS[clave]
    empresa_id: UUID = (
        await session.execute(
            text("""
                INSERT INTO companies (legal_name, tax_id, status)
                VALUES (:n, :t, 'ACTIVE')
                ON CONFLICT (tax_id) WHERE tax_id IS NOT NULL AND deleted_at IS NULL
                    DO UPDATE SET legal_name = EXCLUDED.legal_name
                RETURNING id
            """),
            {"n": nombre, "t": cedula},
        )
    ).scalar_one()
    return empresa_id


async def _usuario(
    session: AsyncSession,
    *,
    email: str,
    nombre: str,
    apellido: str,
    rol: str,
    alcance: str,
    empresa: UUID | None,
) -> UUID:
    user_id: UUID = (
        await session.execute(
            text("""
                INSERT INTO users
                    (email, password_hash, first_name, last_name, status, email_verified_at)
                VALUES (:e, :h, :n, :a, 'ACTIVE', now())
                ON CONFLICT (email) DO UPDATE SET first_name = EXCLUDED.first_name
                RETURNING id
            """),
            {"e": email, "h": hash_password(PASSWORD_DEMO), "n": nombre, "a": apellido},
        )
    ).scalar_one()

    await session.execute(
        text("""
            INSERT INTO user_role_assignments (user_id, role_id, scope_type, company_id)
            SELECT :u, r.id, :s, :c FROM roles r WHERE r.code = :rol
            ON CONFLICT DO NOTHING
        """),
        {"u": user_id, "rol": rol, "s": alcance, "c": empresa},
    )

    if empresa is not None:
        await session.execute(
            text("""
                INSERT INTO company_memberships (company_id, user_id, status)
                VALUES (:c, :u, 'ACTIVE')
                ON CONFLICT DO NOTHING
            """),
            {"c": empresa, "u": user_id},
        )

    return user_id


async def _ubicaciones(session: AsyncSession) -> dict[str, UUID]:
    resultado: dict[str, UUID] = {}
    for pais, ciudad, nombre in UBICACIONES:
        ubicacion_id: UUID = (
            await session.execute(
                text("""
                    INSERT INTO locations (country_code, city_code, location_code, name)
                    VALUES (:p, :c, :cod, :n)
                    ON CONFLICT (location_code) DO UPDATE SET name = EXCLUDED.name
                    RETURNING id
                """),
                {"p": pais, "c": ciudad, "cod": f"{pais}-{ciudad}", "n": nombre},
            )
        ).scalar_one()
        resultado[ciudad] = ubicacion_id
    return resultado


async def _bodega_miami(session: AsyncSession, location_id: UUID) -> UUID:
    """Bodega que emite Warehouse Receipt (ADR-0005).

    Sin esto, ninguna carga de demo exigiría WR y la regla quedaría sin ejercitar
    en la interfaz.
    """
    bodega_id: UUID = (
        await session.execute(
            text("""
                INSERT INTO facilities
                    (location_id, facility_code, facility_type, uses_warehouse_receipt)
                VALUES (:l, 'MIA-01', 'WAREHOUSE', true)
                ON CONFLICT (facility_code) DO UPDATE
                    SET uses_warehouse_receipt = EXCLUDED.uses_warehouse_receipt
                RETURNING id
            """),
            {"l": location_id},
        )
    ).scalar_one()
    return bodega_id


async def _cargas(
    session: AsyncSession,
    *,
    empresas: dict[str, UUID],
    creador: UUID,
    ubicaciones: dict[str, UUID],
    bodega: UUID,
) -> list[UUID]:
    """Cargas repartidas por estado, con fechas escalonadas hacia atrás."""
    ya_hay = (await session.execute(text("SELECT count(*) FROM shipments"))).scalar_one()
    if ya_hay:
        return list(
            (await session.execute(text("SELECT id FROM shipments ORDER BY created_at")))
            .scalars()
            .all()
        )

    # Semilla fija: dos ejecuciones producen los mismos datos, así que una
    # captura de pantalla de ayer sigue teniendo sentido hoy.
    aleatorio = random.Random(20260825)
    creadas: list[UUID] = []
    dias = 90

    for estado, cantidad in REPARTO.items():
        for _ in range(cantidad):
            dias -= 1
            clave = "alfa" if aleatorio.random() < 0.7 else "beta"
            requiere_permiso = aleatorio.random() < 0.15
            momento = datetime.now(UTC) - timedelta(days=max(dias, 1))

            # Las fechas de hito se calculan en Python y no con `CASE WHEN` en
            # SQL: usar el mismo parámetro como valor de columna y dentro de una
            # comparación hace que asyncpg no pueda deducir un tipo único.
            alcanzados = _HITOS_ALCANZADOS[estado]

            shipment_id = (
                await session.execute(
                    text("""
                        INSERT INTO shipments
                            (company_id, created_by, current_status_code,
                             origin_location_id, destination_location_id,
                             origin_facility_id, permit_review_required,
                             created_at, updated_at,
                             received_at, stored_at, dispatched_at, delivered_at)
                        VALUES (:c, :u, :estado, :o, :d, :f, :permiso, :momento, :momento,
                                :recibida, :almacenada, :despachada, :entregada)
                        RETURNING id
                    """),
                    {
                        "c": empresas[clave],
                        "u": creador,
                        "estado": estado.value,
                        "o": ubicaciones["MIA"],
                        "d": ubicaciones["SJO"],
                        "f": bodega,
                        "permiso": requiere_permiso,
                        "momento": momento,
                        "recibida": momento if "received" in alcanzados else None,
                        "almacenada": momento if "stored" in alcanzados else None,
                        "despachada": momento if "dispatched" in alcanzados else None,
                        "entregada": momento if "delivered" in alcanzados else None,
                    },
                )
            ).scalar_one()
            creadas.append(shipment_id)

            await session.execute(
                text("""
                    INSERT INTO shipment_events
                        (shipment_id, event_type, to_status_code, title, occurred_at, actor_user_id)
                    VALUES (:s, 'STATUS_CHANGED', :estado, :titulo, :momento, :actor)
                """),
                {
                    "s": shipment_id,
                    "estado": estado.value,
                    "titulo": f"Estado: {estado.value}",
                    "momento": momento,
                    "actor": creador,
                },
            )

            # Una referencia por carga: el WR, que la bodega de Miami exige.
            await session.execute(
                text("""
                    INSERT INTO shipment_references (shipment_id, reference_type, value)
                    VALUES (:s, 'WR', :wr)
                """),
                {"s": shipment_id, "wr": f"WR{aleatorio.randint(100000, 999999)}"},
            )

    return creadas


async def _requisitos(session: AsyncSession, cargas: list[UUID], actor: UUID) -> None:
    """Abre los requisitos del catálogo en un tercio de las cargas.

    Para que el tablero muestre el contador de pendientes y se vea que "faltan
    documentos" no es un estado de la carga sino un eje aparte.
    """
    from app.modules.shipments import service as shipments

    for shipment_id in cargas[::3]:
        await shipments.sincronizar_requisitos_del_catalogo(
            session, shipment_id=shipment_id, actor_user_id=actor
        )


async def _notificaciones(session: AsyncSession, usuarios: dict[str, UUID], empresa: UUID) -> None:
    ya_hay = (await session.execute(text("SELECT count(*) FROM notifications"))).scalar_one()
    if ya_hay:
        return

    destinatarios = await notificaciones.destinatarios_de_empresa(session, empresa)
    for codigo in ("shipment.dispatched", "shipment.requirement_blocking", "shipment.delivered"):
        await notificaciones.notificar(
            session,
            event_code=codigo,
            destinatarios=destinatarios,
            resource_type="shipment",
            resource_id=(
                await session.execute(text("SELECT id FROM shipments LIMIT 1"))
            ).scalar_one(),
            dedup_key=f"demo-{codigo}",
        )


async def limpiar(session: AsyncSession) -> None:
    """Borra los datos de demo. No toca catálogos ni permisos."""
    await session.execute(
        text("ALTER TABLE shipment_events DISABLE TRIGGER trg_shipment_events_inmutable")
    )
    for tabla in (
        "notification_deliveries",
        "notifications",
        "outbox_events",
        "dispatch_events",
        "dispatch_request_shipments",
        "dispatch_documents",
        "dispatch_requests",
        "shipment_requirements",
        "shipment_documents",
        "shipment_references",
        "shipment_packages",
        "shipment_events",
        "shipments",
        "documents",
        "auth_sessions",
        "company_memberships",
        "user_role_assignments",
        "users",
        "companies",
    ):
        await session.execute(text(f"DELETE FROM {tabla}"))  # noqa: S608
    await session.execute(
        text("ALTER TABLE shipment_events ENABLE TRIGGER trg_shipment_events_inmutable")
    )


async def sembrar(session: AsyncSession) -> dict[str, object]:
    await sembrar_rbac(session)
    await sembrar_estados(session)
    await sembrar_documentos(session)

    empresas = {clave: await _empresa(session, clave) for clave in EMPRESAS}
    ubicaciones = await _ubicaciones(session)
    bodega = await _bodega_miami(session, ubicaciones["MIA"])

    usuarios: dict[str, UUID] = {}
    for email, nombre, apellido, rol, alcance, clave_empresa in CUENTAS:
        usuarios[email] = await _usuario(
            session,
            email=email,
            nombre=nombre,
            apellido=apellido,
            rol=rol,
            alcance=alcance,
            empresa=empresas[clave_empresa] if clave_empresa else None,
        )

    operaciones = usuarios["operaciones@" + DOMINIO]
    cargas = await _cargas(
        session,
        empresas=empresas,
        creador=operaciones,
        ubicaciones=ubicaciones,
        bodega=bodega,
    )
    await _requisitos(session, cargas, operaciones)
    await _notificaciones(session, usuarios, empresas["alfa"])

    return {"usuarios": len(usuarios), "empresas": len(empresas), "cargas": len(cargas)}


async def principal() -> None:
    parser = argparse.ArgumentParser(description="Datos de demostración (solo local).")
    parser.add_argument("--limpiar", action="store_true", help="Borra los datos antes de sembrar.")
    argumentos = parser.parse_args()

    settings = get_settings()
    if settings.environment != "local":
        raise SystemExit(
            f"Este script solo corre en `local`, y el entorno es `{settings.environment}`. "
            "Crea usuarios con una contraseña conocida y publicada."
        )

    async with get_sessionmaker()() as session:
        if argumentos.limpiar:
            await limpiar(session)
        resumen = await sembrar(session)
        await session.commit()

    await get_engine().dispose()

    print("Datos de demostración listos.")
    print(
        f"  empresas: {resumen['empresas']}  usuarios: {resumen['usuarios']}  cargas: {resumen['cargas']}"
    )
    print()
    print(f"  Contraseña de todas las cuentas: {PASSWORD_DEMO}")
    for email, nombre, apellido, rol, _, _ in CUENTAS:
        print(f"    {email:34s} {rol:14s} {nombre} {apellido}")


if __name__ == "__main__":
    asyncio.run(principal())

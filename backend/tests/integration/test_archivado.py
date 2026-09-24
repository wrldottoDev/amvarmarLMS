"""Barrido de archivado (ADR-0007): retención operativa cumplida.

No borra nada — solo marca `archived_at`. Cada excepción del ADR (legal
hold, revisión legacy pendiente, disputa abierta, carga todavía activa) se
prueba por separado: la query es la garantía real, esto la ejercita.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from scripts.seed_rbac import sembrar as sembrar_rbac
from scripts.seed_shipment_statuses import sembrar as sembrar_estados
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.shipments import service
from app.modules.shipments.models import ShipmentStatus
from tests.piezas import sembrar_pieza

pytestmark = pytest.mark.integration


async def _empresa(session: AsyncSession) -> uuid.UUID:
    return (
        await session.execute(
            text("INSERT INTO companies (legal_name, status) VALUES (:n,'ACTIVE') RETURNING id"),
            {"n": f"Archivado {uuid.uuid4().hex[:6]} S.A."},
        )
    ).scalar_one()


async def _usuario(session: AsyncSession) -> uuid.UUID:
    return (
        await session.execute(
            text("""
                INSERT INTO users (email, password_hash, first_name, last_name, status)
                VALUES (:e,'h','N','A','ACTIVE') RETURNING id
            """),
            {"e": f"arch-{uuid.uuid4().hex[:10]}@amvarmar.com"},
        )
    ).scalar_one()


async def _ubicacion(session: AsyncSession, pais: str, ciudad: str, nombre: str) -> uuid.UUID:
    return (
        await session.execute(
            text("""
                INSERT INTO locations (country_code, city_code, location_code, name)
                VALUES (:p,:c,:cod,:n)
                ON CONFLICT (location_code) DO UPDATE SET name = EXCLUDED.name
                RETURNING id
            """),
            {"p": pais, "c": ciudad, "cod": f"{pais}-{ciudad}", "n": nombre},
        )
    ).scalar_one()


async def _carga(
    session: AsyncSession,
    *,
    estado: str = ShipmentStatus.DELIVERED,
    retention_until: datetime | None = None,
    legal_hold: bool = False,
    legacy_review_required: bool = False,
) -> uuid.UUID:
    await sembrar_rbac(session)
    await sembrar_estados(session)
    empresa = await _empresa(session)
    usuario = await _usuario(session)
    origen = await _ubicacion(session, "US", "MIA", "Miami")
    destino = await _ubicacion(session, "CR", "SJO", "San José")
    if retention_until is None:
        retention_until = (
            datetime.now(UTC) - timedelta(days=1)
            if estado in (ShipmentStatus.DELIVERED, ShipmentStatus.CANCELLED)
            else None
        )

    shipment_id = (
        await session.execute(
            text("""
                INSERT INTO shipments
                    (company_id, created_by, current_status_code,
                     origin_location_id, destination_location_id,
                     retention_until, legal_hold, legal_hold_reason, legacy_review_required)
                VALUES (:c,:u,:estado,:o,:d,:retencion,:legal_hold,:legal_hold_reason,:legacy)
                RETURNING id
            """),
            {
                "c": empresa,
                "u": usuario,
                "estado": estado,
                "o": origen,
                "d": destino,
                "retencion": retention_until,
                "legal_hold": legal_hold,
                "legal_hold_reason": "Motivo de prueba." if legal_hold else None,
                "legacy": legacy_review_required,
            },
        )
    ).scalar_one()
    await sembrar_pieza(session, shipment_id)
    return shipment_id


class TestArchivarPendientes:
    async def test_archiva_una_delivered_con_retencion_vencida(self, session: AsyncSession) -> None:
        shipment_id = await _carga(session)

        total = await service.archivar_pendientes(session)

        assert total == 1
        archived_at = (
            await session.execute(
                text("SELECT archived_at FROM shipments WHERE id = :id"), {"id": shipment_id}
            )
        ).scalar_one()
        assert archived_at is not None

    async def test_archiva_una_cancelled_con_retencion_vencida(self, session: AsyncSession) -> None:
        shipment_id = await _carga(session, estado=ShipmentStatus.CANCELLED)

        total = await service.archivar_pendientes(session)

        assert total == 1
        archived_at = (
            await session.execute(
                text("SELECT archived_at FROM shipments WHERE id = :id"), {"id": shipment_id}
            )
        ).scalar_one()
        assert archived_at is not None

    async def test_no_archiva_si_la_retencion_no_vencio(self, session: AsyncSession) -> None:
        shipment_id = await _carga(session, retention_until=datetime.now(UTC) + timedelta(days=30))

        total = await service.archivar_pendientes(session)

        assert total == 0
        archived_at = (
            await session.execute(
                text("SELECT archived_at FROM shipments WHERE id = :id"), {"id": shipment_id}
            )
        ).scalar_one()
        assert archived_at is None

    async def test_no_archiva_una_carga_todavia_activa(self, session: AsyncSession) -> None:
        # Sin retention_until siquiera (chequeo defensivo del ADR): no llegó
        # a DELIVERED/CANCELLED.
        await _carga(session, estado=ShipmentStatus.STORED)

        total = await service.archivar_pendientes(session)

        assert total == 0

    async def test_no_archiva_con_legal_hold_activo(self, session: AsyncSession) -> None:
        shipment_id = await _carga(session, legal_hold=True)

        total = await service.archivar_pendientes(session)

        assert total == 0
        archived_at = (
            await session.execute(
                text("SELECT archived_at FROM shipments WHERE id = :id"), {"id": shipment_id}
            )
        ).scalar_one()
        assert archived_at is None

    async def test_no_archiva_con_revision_legacy_pendiente(self, session: AsyncSession) -> None:
        shipment_id = await _carga(session, legacy_review_required=True)

        total = await service.archivar_pendientes(session)

        assert total == 0
        archived_at = (
            await session.execute(
                text("SELECT archived_at FROM shipments WHERE id = :id"), {"id": shipment_id}
            )
        ).scalar_one()
        assert archived_at is None

    async def test_no_archiva_con_una_inconformidad_abierta(self, session: AsyncSession) -> None:
        shipment_id = await _carga(session)
        cliente = (
            await session.execute(
                text("SELECT created_by FROM shipments WHERE id = :id"), {"id": shipment_id}
            )
        ).scalar_one()
        await session.execute(
            text("""
                INSERT INTO delivery_disputes (shipment_id, raised_by_user_id, reason)
                VALUES (:s, :u, 'No reconozco la entrega.')
            """),
            {"s": shipment_id, "u": cliente},
        )

        total = await service.archivar_pendientes(session)

        assert total == 0
        archived_at = (
            await session.execute(
                text("SELECT archived_at FROM shipments WHERE id = :id"), {"id": shipment_id}
            )
        ).scalar_one()
        assert archived_at is None

    async def test_archiva_varias_a_la_vez(self, session: AsyncSession) -> None:
        for _ in range(3):
            await _carga(session)

        total = await service.archivar_pendientes(session)

        assert total == 3

    async def test_registra_auditoria_con_actor_sistema(self, session: AsyncSession) -> None:
        shipment_id = await _carga(session)

        await service.archivar_pendientes(session)

        fila = (
            await session.execute(
                text("""
                    SELECT actor_user_id, outcome FROM audit_logs
                    WHERE action = 'shipment.archived' AND resource_id = :id
                """),
                {"id": shipment_id},
            )
        ).one()
        assert fila.actor_user_id is None
        assert fila.outcome == "SUCCESS"

    async def test_no_archiva_dos_veces_la_misma_carga(self, session: AsyncSession) -> None:
        await _carga(session)

        primera = await service.archivar_pendientes(session)
        segunda = await service.archivar_pendientes(session)

        assert primera == 1
        assert segunda == 0

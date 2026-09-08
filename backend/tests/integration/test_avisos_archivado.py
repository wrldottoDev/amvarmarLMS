"""Aviso de "documentos por archivar" (ADR-0008): una semana antes de que
`archivar_pendientes` (ADR-0007) los archive de verdad.

Mismas exclusiones que el archivado real (legal hold, revisión legacy,
disputa abierta): avisar de un archivado que no va a ocurrir sería una falsa
alarma.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

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
            {"n": f"Aviso archivado {uuid.uuid4().hex[:6]} S.A."},
        )
    ).scalar_one()


async def _cliente(session: AsyncSession, empresa: uuid.UUID) -> tuple[uuid.UUID, str]:
    email = f"aviso-{uuid.uuid4().hex[:10]}@amvarmar.com"
    user_id = (
        await session.execute(
            text("""
                INSERT INTO users
                    (email, password_hash, first_name, last_name, status, email_verified_at)
                VALUES (:e,'h','N','A','ACTIVE', now())
                RETURNING id
            """),
            {"e": email},
        )
    ).scalar_one()
    await session.execute(
        text(
            "INSERT INTO company_memberships (company_id, user_id, status) VALUES (:c,:u,'ACTIVE')"
        ),
        {"c": empresa, "u": user_id},
    )
    return user_id, email


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
    empresa: uuid.UUID,
    *,
    estado: str = ShipmentStatus.DELIVERED,
    retention_until: datetime | None,
    legal_hold: bool = False,
    legacy_review_required: bool = False,
    invoice: str | None = None,
) -> uuid.UUID:
    await sembrar_rbac(session)
    await sembrar_estados(session)
    usuario, _ = await _cliente(session, empresa)
    origen = await _ubicacion(session, "US", "MIA", "Miami")
    destino = await _ubicacion(session, "CR", "SJO", "San José")

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
    if invoice is not None:
        await session.execute(
            text("""
                INSERT INTO shipment_references (shipment_id, reference_type, value)
                VALUES (:s, 'INVOICE', :v)
            """),
            {"s": shipment_id, "v": invoice},
        )
    return shipment_id


async def _notificaciones(session: AsyncSession, shipment_id: uuid.UUID) -> list[Any]:
    return list(
        (
            await session.execute(
                text("""
                    SELECT event_code, body FROM notifications
                    WHERE resource_type = 'shipment' AND resource_id = :s
                """),
                {"s": shipment_id},
            )
        ).all()
    )


class TestAvisoDeArchivadoProximo:
    async def test_avisa_a_una_carga_dentro_de_la_ventana(self, session: AsyncSession) -> None:
        empresa = await _empresa(session)
        shipment_id = await _carga(
            session,
            empresa,
            retention_until=datetime.now(UTC) + timedelta(days=3),
            invoice="INV-0001",
        )

        total = await service.avisar_archivado_proximo(session)

        assert total == 1
        filas = await _notificaciones(session, shipment_id)
        assert len(filas) == 1
        assert filas[0].event_code == "document.archiving_soon"
        assert "INV-0001" in filas[0].body

    async def test_no_avisa_fuera_de_la_ventana(self, session: AsyncSession) -> None:
        empresa = await _empresa(session)
        shipment_id = await _carga(
            session, empresa, retention_until=datetime.now(UTC) + timedelta(days=30)
        )

        total = await service.avisar_archivado_proximo(session)

        assert total == 0
        assert await _notificaciones(session, shipment_id) == []

    async def test_no_avisa_de_lo_que_ya_vencio(self, session: AsyncSession) -> None:
        """Eso ya lo agarra `archivar_pendientes` — no es un aviso previo."""
        empresa = await _empresa(session)
        shipment_id = await _carga(
            session, empresa, retention_until=datetime.now(UTC) - timedelta(days=1)
        )

        total = await service.avisar_archivado_proximo(session)

        assert total == 0
        assert await _notificaciones(session, shipment_id) == []

    async def test_no_avisa_con_legal_hold(self, session: AsyncSession) -> None:
        empresa = await _empresa(session)
        shipment_id = await _carga(
            session,
            empresa,
            retention_until=datetime.now(UTC) + timedelta(days=3),
            legal_hold=True,
        )

        total = await service.avisar_archivado_proximo(session)

        assert total == 0
        assert await _notificaciones(session, shipment_id) == []

    async def test_no_avisa_con_revision_legacy_pendiente(self, session: AsyncSession) -> None:
        empresa = await _empresa(session)
        shipment_id = await _carga(
            session,
            empresa,
            retention_until=datetime.now(UTC) + timedelta(days=3),
            legacy_review_required=True,
        )

        total = await service.avisar_archivado_proximo(session)

        assert total == 0
        assert await _notificaciones(session, shipment_id) == []

    async def test_no_avisa_con_una_inconformidad_abierta(self, session: AsyncSession) -> None:
        empresa = await _empresa(session)
        shipment_id = await _carga(
            session, empresa, retention_until=datetime.now(UTC) + timedelta(days=3)
        )
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

        total = await service.avisar_archivado_proximo(session)

        assert total == 0
        assert await _notificaciones(session, shipment_id) == []

    async def test_no_avisa_dos_veces_la_misma_carga(self, session: AsyncSession) -> None:
        """El barrido vuelve a encontrar la misma carga en cada corrida — nada
        de lo que consulta cambia solo por avisar — así que la idempotencia la
        garantiza `notificar()` (dedup_key), no el conteo del barrido."""
        empresa = await _empresa(session)
        shipment_id = await _carga(
            session, empresa, retention_until=datetime.now(UTC) + timedelta(days=3)
        )

        await service.avisar_archivado_proximo(session)
        await service.avisar_archivado_proximo(session)

        assert len(await _notificaciones(session, shipment_id)) == 1

    async def test_no_avisa_de_una_carga_ya_archivada(self, session: AsyncSession) -> None:
        empresa = await _empresa(session)
        shipment_id = await _carga(
            session, empresa, retention_until=datetime.now(UTC) + timedelta(days=3)
        )
        await session.execute(
            text("UPDATE shipments SET archived_at = now() WHERE id = :id"), {"id": shipment_id}
        )

        total = await service.avisar_archivado_proximo(session)

        assert total == 0
        assert await _notificaciones(session, shipment_id) == []

    async def test_avisa_varias_a_la_vez(self, session: AsyncSession) -> None:
        empresa = await _empresa(session)
        for _ in range(3):
            await _carga(session, empresa, retention_until=datetime.now(UTC) + timedelta(days=3))

        total = await service.avisar_archivado_proximo(session)

        assert total == 3

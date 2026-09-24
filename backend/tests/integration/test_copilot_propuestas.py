"""Barrido de vencimiento de propuestas (ADR-0012, Fase 7).

Una `PENDING` vencida ya se corrige sola al intentar confirmarla
(`router.confirmar_propuesta`) — esto prueba el camino para cuando nadie lo
intenta nunca: el worker periódico (`app.workers.tasks.copilot`).
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.copilot import propuestas

pytestmark = pytest.mark.integration


async def _usuario(session: AsyncSession) -> uuid.UUID:
    return (
        await session.execute(
            text("""
                INSERT INTO users (email, password_hash, first_name, last_name, status)
                VALUES (:e,'h','N','A','ACTIVE') RETURNING id
            """),
            {"e": f"prop-{uuid.uuid4().hex[:10]}@amvarmar.com"},
        )
    ).scalar_one()


async def _propuesta(
    session: AsyncSession,
    *,
    creador: uuid.UUID,
    status: str = "PENDING",
    expira_en: datetime,
) -> uuid.UUID:
    resuelta_en = None if status == "PENDING" else datetime.now(UTC)
    return (
        await session.execute(
            text("""
                INSERT INTO copilot_action_proposals
                    (created_by, action_code, payload, status, expires_at,
                     resolved_at)
                VALUES (:creador, 'crear_prealerta_borrador', '{}'::jsonb, :status, :expira,
                        :resuelta)
                RETURNING id
            """),
            {
                "creador": creador,
                "status": status,
                "expira": expira_en,
                "resuelta": resuelta_en,
            },
        )
    ).scalar_one()


class TestExpirar:
    async def test_expira_una_pendiente_vencida(self, session: AsyncSession) -> None:
        actor = await _usuario(session)
        vencida = await _propuesta(
            session, creador=actor, expira_en=datetime.now(UTC) - timedelta(minutes=1)
        )

        total = await propuestas.expirar(session)

        assert total == 1
        estado = (
            await session.execute(
                text("SELECT status, resolved_at FROM copilot_action_proposals WHERE id = :id"),
                {"id": vencida},
            )
        ).one()
        assert estado.status == "EXPIRED"
        assert estado.resolved_at is not None

    async def test_no_toca_una_pendiente_todavia_vigente(self, session: AsyncSession) -> None:
        actor = await _usuario(session)
        vigente = await _propuesta(
            session, creador=actor, expira_en=datetime.now(UTC) + timedelta(minutes=30)
        )

        total = await propuestas.expirar(session)

        assert total == 0
        estado = (
            await session.execute(
                text("SELECT status FROM copilot_action_proposals WHERE id = :id"),
                {"id": vigente},
            )
        ).scalar_one()
        assert estado == "PENDING"

    async def test_no_toca_una_ya_resuelta_aunque_este_vencida(self, session: AsyncSession) -> None:
        """`CONFIRMED`/`REJECTED`/`FAILED` no son `PENDING`: el barrido no
        las mira, aunque su `expires_at` ya haya pasado hace rato."""
        actor = await _usuario(session)
        confirmada = await _propuesta(
            session,
            creador=actor,
            status="CONFIRMED",
            expira_en=datetime.now(UTC) - timedelta(days=1),
        )

        total = await propuestas.expirar(session)

        assert total == 0
        estado = (
            await session.execute(
                text("SELECT status FROM copilot_action_proposals WHERE id = :id"),
                {"id": confirmada},
            )
        ).scalar_one()
        assert estado == "CONFIRMED"

    async def test_expira_varias_a_la_vez(self, session: AsyncSession) -> None:
        actor = await _usuario(session)
        for _ in range(3):
            await _propuesta(
                session, creador=actor, expira_en=datetime.now(UTC) - timedelta(minutes=1)
            )

        total = await propuestas.expirar(session)

        assert total == 3

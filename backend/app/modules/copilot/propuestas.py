"""Comandos sobre `copilot_action_proposals` (ADR-0012, Fase 4).

`router.py` sigue teniendo su propio SQL para leer y resolver una propuesta
(`_propuesta_del_actor`, confirmar, rechazar) — eso no cambia acá. Este módulo
existe para que los ejecutores de ESCRITURA (`executors_escritura.py`,
`confirmaciones.py`) tengan de dónde crear y releer una propuesta sin escribir
SQL suelto en el ejecutor: "cero SQL directo desde los executors" (ADR-0012).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.copilot.acciones import AccionCopilot


async def crear(
    session: AsyncSession,
    *,
    creador: UUID,
    company_id: UUID | None,
    action_code: AccionCopilot,
    payload: dict[str, Any],
    expira_en: datetime,
) -> UUID:
    """La ÚNICA escritura que un ejecutor de ESCRITURA hace durante el turno
    del modelo (ADR-0012): esta fila, nunca una tabla de dominio."""
    propuesta_id: UUID = (
        await session.execute(
            text("""
                INSERT INTO copilot_action_proposals
                    (created_by, company_id, action_code, payload, expires_at)
                VALUES (:creador, :company_id, :action_code, CAST(:payload AS JSONB), :expira)
                RETURNING id
            """),
            {
                "creador": creador,
                "company_id": company_id,
                "action_code": action_code.value,
                "payload": json.dumps(payload, default=str),
                "expira": expira_en,
            },
        )
    ).scalar_one()
    return propuesta_id


async def obtener(session: AsyncSession, propuesta_id: UUID) -> Any:
    """Para un ejecutor de CONFIRMACIÓN: `router.confirmar_propuesta` ya
    bloqueó y validó la fila (dueño, `PENDING`, no vencida) antes de llamarlo,
    pero el contrato `EjecutorConfirmacion` solo le pasa el id — así que
    releer acá es la misma fila que el router ya tiene tomada, no una
    segunda oportunidad de leer algo ajeno."""
    return (
        await session.execute(
            text("""
                SELECT id, created_by, company_id, action_code, payload
                FROM copilot_action_proposals WHERE id = :id
            """),
            {"id": propuesta_id},
        )
    ).one()


async def expirar(session: AsyncSession, *, limite: int = 500) -> int:
    """Barrido de vencimiento: una `PENDING` con `expires_at` pasado ya se
    marca `EXPIRED` al intentar confirmarla (`router.confirmar_propuesta`),
    pero si nadie lo intenta nunca se corrige sola — esto la pone al día
    igual, para que un listado o un reporte no la sigan mostrando como
    accionable. Usa el índice parcial `ix_copilot_proposals_vencimiento`
    (`models.py`), pensado para esto desde que se creó la tabla."""
    resultado = await session.execute(
        text("""
            UPDATE copilot_action_proposals
            SET status = 'EXPIRED', resolved_at = now()
            WHERE id IN (
                SELECT id FROM copilot_action_proposals
                WHERE status = 'PENDING' AND expires_at <= now()
                LIMIT :limite
            )
            RETURNING id
        """),
        {"limite": limite},
    )
    return len(resultado.all())

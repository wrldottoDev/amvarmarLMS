"""Escritura de auditoría.

Se llama DENTRO de la transacción de la operación auditada: si el cambio de
negocio hace rollback, su registro de auditoría tampoco queda. Un log que
afirma algo que no ocurrió es peor que no tenerlo.
"""

import json
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.models import Outcome
from app.modules.audit.redaction import redactar


async def registrar(
    session: AsyncSession,
    *,
    action: str,
    resource_type: str,
    outcome: Outcome = Outcome.SUCCESS,
    actor_user_id: UUID | None = None,
    company_id: UUID | None = None,
    resource_id: UUID | None = None,
    before_data: dict[str, Any] | None = None,
    after_data: dict[str, Any] | None = None,
    request_id: UUID | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    reason: str | None = None,
) -> None:
    """Escribe una entrada. Los snapshots se redactan aquí, sin excepción.

    La redacción vive en esta función y no en el caller a propósito: si cada
    llamador tuviera que acordarse de redactar, tarde o temprano uno no lo hace.
    """
    await session.execute(
        text("""
            INSERT INTO audit_logs (
                actor_user_id, company_id, action, resource_type, resource_id,
                outcome, before_data, after_data, request_id, ip_address,
                user_agent, reason
            )
            VALUES (
                :actor_user_id, :company_id, :action, :resource_type, :resource_id,
                :outcome, CAST(:before_data AS JSONB), CAST(:after_data AS JSONB),
                :request_id, :ip_address, :user_agent, :reason
            )
        """),
        {
            "actor_user_id": actor_user_id,
            "company_id": company_id,
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "outcome": outcome.value,
            "before_data": _a_json(before_data),
            "after_data": _a_json(after_data),
            "request_id": request_id,
            "ip_address": ip_address,
            "user_agent": user_agent,
            "reason": reason,
        },
    )


def _a_json(datos: dict[str, Any] | None) -> str | None:
    if datos is None:
        return None
    return json.dumps(redactar(datos), default=str, ensure_ascii=False)

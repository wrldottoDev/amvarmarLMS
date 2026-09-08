"""Propuestas de acción del asistente (ADR-0012, enmienda 2026-09).

Antes de esta fase, `PropuestaAccion` (en `tools.py`) era un modelo Pydantic
sin tabla: no había estado, vencimiento real, ni forma de impedir que se
confirmara dos veces. Esta tabla es la que hace cumplir "de un solo uso": la
confirmación bloquea la fila (`SELECT ... FOR UPDATE`), y solo una gana.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, UUIDPk


class EstadoPropuesta(StrEnum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"


class CopilotActionProposal(Base):
    """Una acción de escritura que el modelo preparó y nadie ejecutó todavía.

    `resource_versions` guarda el `row_version` de cada recurso afectado en el
    momento de proponer. Al confirmar, si el recurso cambió mientras la
    propuesta esperaba, el service de dominio que se llama en la confirmación
    responde 409 igual que le respondería a cualquier otro llamador — la
    propuesta no se salta esa garantía, la atraviesa.
    """

    __tablename__ = "copilot_action_proposals"

    id: Mapped[UUIDPk]
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    # Nulo para personal interno sin empresa. Se deriva del JWT del actor al
    # crear la propuesta — nunca de un argumento que el modelo eligió.
    company_id: Mapped[UUID | None] = mapped_column(ForeignKey("companies.id", ondelete="RESTRICT"))

    action_code: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    resource_versions: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))
    status: Mapped[str] = mapped_column(String(16), server_default=text("'PENDING'"))

    expires_at: Mapped[datetime]
    # La confirmación reutiliza el mecanismo genérico de `idempotency_keys`
    # (`core/idempotency.py`), el mismo que usa el resto de la API — no una
    # columna propia. Esta tabla no guarda la clave.
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    resolved_at: Mapped[datetime | None]

    __table_args__ = (
        # `action_code` no tiene FK a una tabla de catálogo a propósito: el
        # registro cerrado vive en código (`acciones.AccionCopilot`), no en la
        # base. El CHECK es la última defensa si alguien inserta a mano.
        # Se actualiza junto con el StrEnum.
        CheckConstraint(
            "action_code IN ('procesar_factura_ocr', 'crear_prealerta_borrador')",
            name="action_code_valido",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'CONFIRMED', 'REJECTED', 'EXPIRED', 'FAILED')",
            name="status_valido",
        ),
        CheckConstraint(
            "status = 'PENDING' OR resolved_at IS NOT NULL",
            name="resuelta_tiene_fecha",
        ),
        # El listado del actor filtra por dueño y estado — la consulta más
        # frecuente una vez que hay uso real.
        Index(
            "ix_copilot_proposals_creador_estado",
            "created_by",
            "status",
            postgresql_where=text("status = 'PENDING'"),
        ),
        # El barrido de vencimiento (Fase 7) recorre solo las pendientes.
        Index(
            "ix_copilot_proposals_vencimiento",
            "expires_at",
            postgresql_where=text("status = 'PENDING'"),
        ),
    )

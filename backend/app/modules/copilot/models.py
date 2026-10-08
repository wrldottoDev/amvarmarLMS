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

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
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
            "action_code IN ('procesar_factura_ocr', 'crear_prealerta_borrador', "
            "'proponer_cambio_estado', 'proponer_despacho')",
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


class CopilotConversation(Base):
    """Una conversación con AMVI, para retomarla después (ADR-0012, enmienda
    2026-10-08). Se borra sola a los 180 días sin uso.

    `client_key` es el id que el frontend genera para la conversación y que ya
    namespacea el tope de tokens en Redis: así el chat sigue mandando lo mismo
    y el backend sabe a qué conversación guardada pertenece cada turno.
    """

    __tablename__ = "copilot_conversations"

    id: Mapped[UUIDPk]
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    company_id: Mapped[UUID | None] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    client_key: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(server_default=text("now()"))

    __table_args__ = (
        UniqueConstraint("user_id", "client_key", name="uq_copilot_conversations_usuario_clave"),
        Index("ix_copilot_conversations_usuario_reciente", "user_id", "updated_at"),
    )


class CopilotMessage(Base):
    """Un mensaje guardado. `feedback` es el 👍 (1) / 👎 (-1) de quien
    preguntó: lo que Operaciones revisa para mejorar las guías."""

    __tablename__ = "copilot_messages"

    id: Mapped[UUIDPk]
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("copilot_conversations.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    feedback: Mapped[int | None] = mapped_column(SmallInteger)
    feedback_comment: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))

    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant')", name="rol_valido"),
        CheckConstraint("feedback IS NULL OR feedback IN (-1, 1)", name="feedback_valido"),
        Index("ix_copilot_messages_conversacion", "conversation_id", "created_at"),
        Index(
            "ix_copilot_messages_mal_calificados",
            "created_at",
            postgresql_where=text("feedback = -1"),
        ),
    )


class CopilotKnowledgeEntry(Base):
    """Una guía que escribió Operaciones desde la pantalla de aprendizaje.
    Se suma a las de `copilot/conocimiento/*.md` sin desplegar código."""

    __tablename__ = "copilot_knowledge_entries"

    id: Mapped[UUIDPk]
    title: Mapped[str] = mapped_column(String(160))
    keywords: Mapped[str] = mapped_column(String(500))
    content: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(server_default=text("true"))
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))


class CopilotUnansweredTopic(Base):
    """Algo que alguien preguntó y AMVI no tenía guía para contestar."""

    __tablename__ = "copilot_unanswered_topics"

    id: Mapped[UUIDPk]
    topic: Mapped[str] = mapped_column(String(300))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    resolved_at: Mapped[datetime | None]

    __table_args__ = (
        Index(
            "ix_copilot_unanswered_pendientes",
            "created_at",
            postgresql_where=text("resolved_at IS NULL"),
        ),
    )

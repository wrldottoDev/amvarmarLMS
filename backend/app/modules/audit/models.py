from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, SmallInteger, String, Text, text
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, UUIDPk


class Outcome(StrEnum):
    SUCCESS = "SUCCESS"
    DENIED = "DENIED"
    FAILED = "FAILED"


class AuditLog(Base):
    """Bitácora append-only. Nadie hace UPDATE ni DELETE sobre esta tabla.

    `before_data` y `after_data` pasan siempre por `redaction.redactar()`: la
    bitácora vive 2 años (ADR-0007) y un secreto que entre aquí queda expuesto
    todo ese tiempo en un lugar donde nadie lo busca.
    """

    __tablename__ = "audit_logs"

    id: Mapped[UUIDPk]
    occurred_at: Mapped[datetime] = mapped_column(server_default=text("now()"))

    # SET NULL, no CASCADE: borrar un usuario no puede borrar el rastro de lo
    # que hizo. La auditoría sobrevive al actor.
    actor_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    company_id: Mapped[UUID | None] = mapped_column(ForeignKey("companies.id", ondelete="SET NULL"))

    action: Mapped[str] = mapped_column(String(100))
    resource_type: Mapped[str] = mapped_column(String(80))
    resource_id: Mapped[UUID | None]

    outcome: Mapped[str] = mapped_column(String(16))

    before_data: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    after_data: Mapped[dict[str, object] | None] = mapped_column(JSONB)

    request_id: Mapped[UUID | None]
    ip_address: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("outcome IN ('SUCCESS', 'DENIED', 'FAILED')", name="outcome_valido"),
        Index("ix_audit_logs_company_occurred", "company_id", text("occurred_at DESC")),
        Index("ix_audit_logs_actor_occurred", "actor_user_id", text("occurred_at DESC")),
        Index(
            "ix_audit_logs_recurso_occurred",
            "resource_type",
            "resource_id",
            text("occurred_at DESC"),
        ),
    )


class IdempotencyKey(Base):
    """Respuesta guardada de una operación con `Idempotency-Key`.

    Reintentar una creación tras un timeout de red no debe crear dos cargas ni
    dos solicitudes de despacho.
    """

    __tablename__ = "idempotency_keys"

    id: Mapped[UUIDPk]
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(128))

    # Hash del cuerpo original. Si llega la misma clave con un cuerpo distinto,
    # es un error del cliente y se rechaza en vez de devolver una respuesta que
    # no corresponde a lo que pidió.
    request_hash: Mapped[str] = mapped_column(String(64))

    status_code: Mapped[int | None] = mapped_column(SmallInteger)
    response_body: Mapped[dict[str, object] | None] = mapped_column(JSONB)

    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    expires_at: Mapped[datetime]

    __table_args__ = (
        Index("uq_idempotency_keys_user_key", "user_id", "key", unique=True),
        Index("ix_idempotency_keys_expires_at", "expires_at"),
    )


class OutboxEvent(Base):
    """Transactional outbox (Fase 4).

    Se crea desde Fase 1 por recomendación de ADR-0008: los eventos críticos de
    seguridad (reuse detection, cambio de contraseña) nacen en el Paso 1.6/1.7,
    antes de que exista el worker. Escribirlos ya deja el rastro y permite
    entregarlos cuando el worker exista, sin perder los de este período.
    """

    __tablename__ = "outbox_events"

    id: Mapped[UUIDPk]
    aggregate_type: Mapped[str] = mapped_column(String(60))
    aggregate_id: Mapped[UUID]
    event_type: Mapped[str] = mapped_column(String(100))

    # Mínimo y sin secretos: el worker recarga lo que necesite desde la base.
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)

    # PENDING, DONE o FAILED. NO existe PROCESSING a propósito: el worker
    # reclama con `FOR UPDATE SKIP LOCKED`, y ese lock lo suelta PostgreSQL
    # solo si el proceso muere. Una columna PROCESSING sobreviviría a la
    # muerte del worker y dejaría el evento trabado hasta que alguien lo
    # rescatara a mano — justo lo que este paso tiene que evitar.
    status: Mapped[str] = mapped_column(String(12), server_default=text("'PENDING'"))

    # Idempotencia en el ORIGEN: si el mismo hecho de negocio se escribe dos
    # veces, la segunda choca contra el índice único en vez de generar una
    # notificación duplicada.
    dedup_key: Mapped[str | None] = mapped_column(String(200))

    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    # Cuándo puede volver a intentarse. El backoff lo empuja hacia adelante.
    available_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    processed_at: Mapped[datetime | None]
    attempt_count: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    last_error_code: Mapped[str | None] = mapped_column(String(80))

    __table_args__ = (
        CheckConstraint("status IN ('PENDING', 'DONE', 'FAILED')", name="outbox_status_valido"),
        # Un evento terminado tiene fecha; uno pendiente no. Sin esto, un bug
        # podría dejar DONE sin `processed_at` y el rastro no serviría.
        CheckConstraint(
            "(status = 'PENDING') = (processed_at IS NULL)",
            name="outbox_procesado_tiene_fecha",
        ),
        Index(
            "uq_outbox_events_dedup",
            "dedup_key",
            unique=True,
            postgresql_where=text("dedup_key IS NOT NULL"),
        ),
        # Índice parcial: el worker solo consulta lo pendiente, y esta tabla
        # acumula millones de filas ya procesadas.
        Index(
            "ix_outbox_events_pendientes",
            "available_at",
            "created_at",
            postgresql_where=text("status = 'PENDING'"),
        ),
    )

"""Notificaciones y su registro de entrega (Paso 4.2).

Dos tablas separadas a propósito:

- `notifications` es el hecho de negocio: a este usuario hay que avisarle esto.
  Es lo que la bandeja in-app muestra y lo que se marca como leído.
- `notification_deliveries` es cada intento por cada canal. Un mismo aviso puede
  entregarse bien in-app y fallar por correo; mezclarlos en una sola tabla
  obligaría a elegir cuál de los dos resultados guardar.

Alcance de este paso: `IN_APP` y `EMAIL` (ADR-0008). `PUSH` se suma en la fase
de apps móviles y `WHATSAPP` en una fase futura.
"""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, SmallInteger, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, UUIDPk


class Channel(StrEnum):
    IN_APP = "IN_APP"
    EMAIL = "EMAIL"


class DeliveryStatus(StrEnum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"
    # El usuario no tiene correo verificado, o el canal no aplica. No es un
    # error: distinguirlo de FAILED evita perseguir fallos que no existen.
    SKIPPED = "SKIPPED"


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[UUIDPk]
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # Redundante con el usuario, pero deja filtrar la bandeja por empresa sin
    # cruzar tablas, y sobrevive si el usuario cambia de empresa.
    company_id: Mapped[UUID | None] = mapped_column(ForeignKey("companies.id", ondelete="SET NULL"))

    event_code: Mapped[str] = mapped_column(String(100))
    # Crítico según el catálogo de ADR-0008. Se guarda en la fila y no se
    # recalcula: si mañana un evento cambia de categoría, lo ya enviado debe
    # seguir contando la verdad de cuando se envió.
    is_critical: Mapped[bool] = mapped_column(server_default=text("false"))

    title: Mapped[str] = mapped_column(String(180))
    body: Mapped[str] = mapped_column(Text)

    # A dónde lleva el aviso. La interfaz arma la ruta; acá solo el qué y el cuál.
    resource_type: Mapped[str | None] = mapped_column(String(60))
    resource_id: Mapped[UUID | None]

    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    read_at: Mapped[datetime | None]

    __table_args__ = (
        # La bandeja pagina por fecha descendente y el contador solo mira lo no
        # leído, que con el tiempo es una fracción del total.
        Index(
            "ix_notifications_bandeja",
            "user_id",
            text("created_at DESC"),
            "id",
        ),
        Index(
            "ix_notifications_no_leidas",
            "user_id",
            postgresql_where=text("read_at IS NULL"),
        ),
    )


class NotificationDelivery(Base):
    __tablename__ = "notification_deliveries"

    id: Mapped[UUIDPk]
    notification_id: Mapped[UUID] = mapped_column(
        ForeignKey("notifications.id", ondelete="CASCADE")
    )
    channel: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(12), server_default=text("'PENDING'"))

    attempt_count: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    # Motivo del último fallo. Sin esto, "no llegó el correo" es imposible de
    # diagnosticar sin acceso al relay.
    last_error: Mapped[str | None] = mapped_column(Text)
    # A dónde se envió, tal como estaba en ese momento: si el usuario cambia de
    # correo después, el registro tiene que seguir diciendo a dónde fue.
    target: Mapped[str | None] = mapped_column(String(320))
    metadata_: Mapped[dict[str, object] | None] = mapped_column("metadata", JSONB)

    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    sent_at: Mapped[datetime | None]

    __table_args__ = (
        CheckConstraint("channel IN ('IN_APP', 'EMAIL')", name="channel_valido"),
        CheckConstraint(
            "status IN ('PENDING', 'SENT', 'FAILED', 'SKIPPED')", name="delivery_status_valido"
        ),
        # Un canal, una entrega por notificación. Sin esto un reintento del
        # worker crearía filas nuevas y el registro diría que se mandaron tres
        # correos cuando se mandó uno.
        Index(
            "uq_notification_deliveries_canal",
            "notification_id",
            "channel",
            unique=True,
        ),
    )

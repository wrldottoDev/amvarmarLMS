from datetime import date, datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, UUIDPk


class DispatchMethod(StrEnum):
    SEA = "SEA"
    AIR = "AIR"
    LAND = "LAND"


class DispatchStatus(StrEnum):
    """Estados de una solicitud de despacho."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    PREPARING = "PREPARING"
    DISPATCHED = "DISPATCHED"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


# Estados en los que la solicitud ya no retiene las cargas: al llegar a
# cualquiera de ellos se libera `released_at` y las cargas quedan disponibles
# para otra solicitud.
ESTADOS_FINALES: frozenset[str] = frozenset(
    {DispatchStatus.COMPLETED, DispatchStatus.REJECTED, DispatchStatus.CANCELLED}
)


class DispatchEventType(StrEnum):
    CREATED = "CREATED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    PREPARING = "PREPARING"
    DISPATCHED = "DISPATCHED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    NOTE = "NOTE"


class DispatchRequest(Base):
    __tablename__ = "dispatch_requests"

    id: Mapped[UUIDPk]
    # Código legible, formato DSP-YYYY-NNNNNN. Único, pero no es la PK.
    dispatch_number: Mapped[str] = mapped_column(
        String(32), unique=True, server_default=text("siguiente_dispatch_number()")
    )

    company_id: Mapped[UUID] = mapped_column(ForeignKey("companies.id", ondelete="RESTRICT"))
    requested_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))

    method: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))

    delivery_address: Mapped[str | None] = mapped_column(Text)
    instructions: Mapped[str | None] = mapped_column(Text)
    requested_pickup_date: Mapped[date | None]

    approved_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    rejected_reason: Mapped[str | None] = mapped_column(Text)

    requested_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    approved_at: Mapped[datetime | None]
    dispatched_at: Mapped[datetime | None]
    completed_at: Mapped[datetime | None]

    # Bloqueo optimista, igual que en `shipments`.
    row_version: Mapped[int] = mapped_column(server_default=text("1"))
    updated_at: Mapped[datetime] = mapped_column(
        server_default=text("now()"), onupdate=text("now()")
    )

    __table_args__ = (
        CheckConstraint("method IN ('SEA', 'AIR', 'LAND')", name="method_valido"),
        CheckConstraint(
            "status IN ('PENDING', 'APPROVED', 'PREPARING', 'DISPATCHED', "
            "'COMPLETED', 'REJECTED', 'CANCELLED')",
            name="status_valido",
        ),
        # Rechazar sin motivo deja la decisión sin explicación: quien reciba el
        # rechazo no sabría qué corregir.
        CheckConstraint(
            "status <> 'REJECTED' OR rejected_reason IS NOT NULL",
            name="rechazo_exige_motivo",
        ),
        CheckConstraint("row_version > 0", name="row_version_positiva"),
        Index(
            "ix_dispatch_requests_empresa_estado", "company_id", "status", text("requested_at DESC")
        ),
    )


class DispatchRequestShipment(Base):
    """Qué cargas reclama una solicitud.

    El índice único parcial sobre `shipment_id WHERE released_at IS NULL` es lo
    que hace imposible que dos solicitudes activas reclamen la misma carga. El
    `FOR UPDATE` del servicio existe para dar un error claro; la garantía es
    este índice, que vale aunque alguien inserte por SQL directo.
    """

    __tablename__ = "dispatch_request_shipments"

    dispatch_request_id: Mapped[UUID] = mapped_column(
        ForeignKey("dispatch_requests.id", ondelete="CASCADE"), primary_key=True
    )
    shipment_id: Mapped[UUID] = mapped_column(
        ForeignKey("shipments.id", ondelete="RESTRICT"), primary_key=True
    )
    added_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    # Se llena al cerrar, rechazar o cancelar: libera la carga.
    released_at: Mapped[datetime | None]

    __table_args__ = (
        Index(
            "uq_active_dispatch_per_shipment",
            "shipment_id",
            unique=True,
            postgresql_where=text("released_at IS NULL"),
        ),
    )


class DispatchEvent(Base):
    """Historia de la solicitud. Append-only, como `shipment_events`."""

    __tablename__ = "dispatch_events"

    id: Mapped[UUIDPk]
    dispatch_request_id: Mapped[UUID] = mapped_column(
        ForeignKey("dispatch_requests.id", ondelete="RESTRICT")
    )

    event_type: Mapped[str] = mapped_column(String(40))
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str | None] = mapped_column(String(20))
    notes: Mapped[str | None] = mapped_column(Text)

    actor_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    occurred_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    datos: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSONB, server_default=text("'{}'::jsonb")
    )

    __table_args__ = (
        Index(
            "ix_dispatch_events_solicitud", "dispatch_request_id", text("occurred_at DESC"), "id"
        ),
    )


class DispatchDocument(Base):
    """Documentos del despacho (BL, guías). Distintos de los de cada carga."""

    __tablename__ = "dispatch_documents"

    dispatch_request_id: Mapped[UUID] = mapped_column(
        ForeignKey("dispatch_requests.id", ondelete="CASCADE"), primary_key=True
    )
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="RESTRICT"), primary_key=True
    )
    document_type_id: Mapped[UUID] = mapped_column(
        ForeignKey("document_types.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))

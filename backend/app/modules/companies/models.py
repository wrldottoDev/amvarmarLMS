from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin, UUIDPk


class CompanyStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    CLOSED = "CLOSED"


class MembershipStatus(StrEnum):
    INVITED = "INVITED"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"


class Company(Base, TimestampMixin):
    """Empresa cliente de AMVARMAR (sección 3.3).

    AMVARMAR no se representa a sí misma como fila de esta tabla: su personal
    interno se identifica por rol con alcance GLOBAL (ADR-0011).
    """

    __tablename__ = "companies"

    id: Mapped[UUIDPk]

    legal_name: Mapped[str] = mapped_column(String(180))
    trade_name: Mapped[str | None] = mapped_column(String(180))
    tax_id: Mapped[str | None] = mapped_column(CITEXT)

    status: Mapped[str] = mapped_column(String(20))

    # Nullable: las 11 empresas que llegan del sistema legacy (Fase 5) no tienen
    # registrado quién las creó. Inventar un creador sería falsear auditoría.
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))

    deleted_at: Mapped[datetime | None]

    __table_args__ = (
        CheckConstraint(
            "status IN ('ACTIVE', 'SUSPENDED', 'CLOSED')",
            name="status_valido",
        ),
        # Índice único parcial: dos empresas no pueden compartir cédula jurídica,
        # pero el campo es opcional y una empresa borrada libera su tax_id para
        # que pueda volver a darse de alta.
        Index(
            "uq_companies_tax_id_activo",
            "tax_id",
            unique=True,
            postgresql_where=text("tax_id IS NOT NULL AND deleted_at IS NULL"),
        ),
    )


class CompanyMembership(Base):
    """Vincula un usuario cliente con su empresa (sección 3.3, ADR-0011).

    El personal interno de AMVARMAR NO tiene fila aquí. Tener membership es lo
    que define a un usuario como cliente.
    """

    __tablename__ = "company_memberships"

    id: Mapped[UUIDPk]

    company_id: Mapped[UUID] = mapped_column(ForeignKey("companies.id", ondelete="RESTRICT"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))

    status: Mapped[str] = mapped_column(String(20))
    is_primary: Mapped[bool] = mapped_column(server_default=text("false"))
    joined_at: Mapped[datetime | None]

    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))

    __table_args__ = (
        CheckConstraint(
            "status IN ('INVITED', 'ACTIVE', 'SUSPENDED')",
            name="status_valido",
        ),
        # ADR-0011: un usuario pertenece a UNA sola empresa. Esta restricción es
        # más estricta que el UNIQUE(company_id, user_id) del documento de
        # arquitectura, que permitiría varias.
        # Habilitar multi-empresa en el futuro = eliminar esta restricción y
        # agregar UNIQUE(company_id, user_id). No requiere migrar datos.
        UniqueConstraint("user_id", name="uq_company_memberships_user_id"),
    )

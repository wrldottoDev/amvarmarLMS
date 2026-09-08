from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, CITEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin, UUIDPk


class ScopeType(StrEnum):
    """Alcance de una asignación de rol (ADR-0004)."""

    GLOBAL = "GLOBAL"
    ORGANIZATION = "ORGANIZATION"
    ASSIGNED = "ASSIGNED"
    OWN = "OWN"


class RoleCode(StrEnum):
    SUPER_ADMIN = "SUPER_ADMIN"
    OPS_ADMIN = "OPS_ADMIN"
    OPS_AGENT = "OPS_AGENT"
    CLIENT_ADMIN = "CLIENT_ADMIN"
    CLIENT_USER = "CLIENT_USER"


class Role(Base, TimestampMixin):
    __tablename__ = "roles"

    id: Mapped[UUIDPk]
    code: Mapped[str] = mapped_column(CITEXT, unique=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)

    # ADR-0004: el alcance concreto vive en la asignación, no en el rol. Aquí se
    # declara qué alcances admite el rol, porque un OPS_AGENT puede ser GLOBAL o
    # ASSIGNED según el puesto de cada persona. Es validación, no el valor real.
    allowed_scopes: Mapped[list[str]] = mapped_column(ARRAY(String(16)))

    # Los roles del catálogo no se pueden borrar ni renombrar desde la interfaz:
    # el código de la aplicación depende de sus códigos.
    is_system: Mapped[bool] = mapped_column(server_default=text("false"))

    __table_args__ = (
        CheckConstraint("cardinality(allowed_scopes) > 0", name="al_menos_un_alcance"),
        CheckConstraint(
            "allowed_scopes <@ ARRAY['GLOBAL','ORGANIZATION','ASSIGNED','OWN']::varchar[]",
            name="alcances_validos",
        ),
    )


class Permission(Base):
    __tablename__ = "permissions"

    id: Mapped[UUIDPk]
    # Formato `recurso.accion`, ej. `shipments.read`. CITEXT evita que
    # `Shipments.Read` y `shipments.read` convivan como permisos distintos.
    code: Mapped[str] = mapped_column(CITEXT, unique=True)
    resource: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(80))
    description: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))


class RolePermission(Base):
    """Qué permisos otorga cada rol. PK compuesta, sin id propio."""

    __tablename__ = "role_permissions"

    role_id: Mapped[UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    permission_id: Mapped[UUID] = mapped_column(
        ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True
    )
    granted_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    granted_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))


class UserRoleAssignment(Base):
    """Asignación de un rol a un usuario, con su alcance concreto.

    Reglas de coherencia (sección 3.3 + ADR-0004), impuestas por CHECK para que
    valgan también si alguien inserta por SQL directo:
    - `ORGANIZATION` exige `company_id`.
    - `GLOBAL`, `ASSIGNED` y `OWN` exigen `company_id IS NULL`.
    """

    __tablename__ = "user_role_assignments"

    id: Mapped[UUIDPk]

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    role_id: Mapped[UUID] = mapped_column(ForeignKey("roles.id", ondelete="RESTRICT"))

    scope_type: Mapped[str] = mapped_column(String(16))
    company_id: Mapped[UUID | None] = mapped_column(ForeignKey("companies.id", ondelete="RESTRICT"))

    assigned_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    assigned_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    expires_at: Mapped[datetime | None]

    __table_args__ = (
        CheckConstraint(
            "scope_type IN ('GLOBAL', 'ORGANIZATION', 'ASSIGNED', 'OWN')",
            name="scope_type_valido",
        ),
        CheckConstraint(
            "(scope_type = 'ORGANIZATION' AND company_id IS NOT NULL) "
            "OR (scope_type <> 'ORGANIZATION' AND company_id IS NULL)",
            name="company_id_coherente_con_scope",
        ),
        # Dos índices únicos parciales en vez de uno solo: en PostgreSQL, NULL no
        # colisiona con NULL, así que UNIQUE(user_id, role_id, company_id) dejaría
        # duplicar asignaciones globales del mismo rol al mismo usuario.
        Index(
            "uq_user_role_assignments_global",
            "user_id",
            "role_id",
            unique=True,
            postgresql_where=text("company_id IS NULL"),
        ),
        Index(
            "uq_user_role_assignments_company",
            "user_id",
            "role_id",
            "company_id",
            unique=True,
            postgresql_where=text("company_id IS NOT NULL"),
        ),
    )

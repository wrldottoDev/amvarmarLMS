from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, SmallInteger, String, text
from sqlalchemy.dialects.postgresql import CITEXT, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin, UUIDPk


class UserStatus(StrEnum):
    INVITED = "INVITED"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    DISABLED = "DISABLED"


class User(Base, TimestampMixin):
    """Sección 3.3 del documento de arquitectura.

    No existe `is_staff`: la autorización sale siempre del rol asignado, nunca de
    un flag en el usuario. Un usuario interno de AMVARMAR se distingue por tener
    rol con alcance GLOBAL y ninguna fila en `company_memberships` (ADR-0011).
    """

    __tablename__ = "users"

    id: Mapped[UUIDPk]

    # CITEXT: la unicidad del email es insensible a mayúsculas a nivel de base.
    # No depende de que la aplicación recuerde normalizar antes de insertar.
    email: Mapped[str] = mapped_column(CITEXT, unique=True)

    # Ancho suficiente para Argon2id y para el hash PBKDF2 heredado de Django,
    # que convive durante la migración hasta el primer login de cada usuario.
    password_hash: Mapped[str] = mapped_column(String(255))

    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(150))
    phone: Mapped[str | None] = mapped_column(String(32))

    status: Mapped[str] = mapped_column(String(20))
    must_change_password: Mapped[bool] = mapped_column(server_default=text("false"))
    email_verified_at: Mapped[datetime | None]

    failed_login_attempts: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    locked_until: Mapped[datetime | None]
    password_changed_at: Mapped[datetime | None]

    # Aumenta al cambiar roles o permisos del usuario. Permite invalidar
    # decisiones de autorización cacheadas en Redis (Paso 1.4) sin rastrear
    # cada clave por separado.
    authz_version: Mapped[int] = mapped_column(server_default=text("1"))

    last_login_at: Mapped[datetime | None]
    deleted_at: Mapped[datetime | None]

    __table_args__ = (
        CheckConstraint(
            "status IN ('INVITED', 'ACTIVE', 'SUSPENDED', 'DISABLED')",
            name="status_valido",
        ),
        CheckConstraint("failed_login_attempts >= 0", name="intentos_fallidos_no_negativos"),
        CheckConstraint("authz_version > 0", name="authz_version_positiva"),
    )


class ColumnPreference(Base):
    """Qué columnas ve cada persona en cada listado.

    El sistema viejo lo tenía en dos tablas —una para administración y otra para
    clientes— porque eran dos plantillas distintas. Acá es una sola con la
    pantalla como parte de la clave: agregar un listado nuevo no debería obligar
    a crear otra tabla.

    Es preferencia de interfaz, no configuración del negocio: si se pierde, la
    persona vuelve a marcar sus columnas y no pasa nada más.
    """

    __tablename__ = "column_preferences"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    # `shipments`, `dispatches`… La pantalla a la que aplica.
    vista: Mapped[str] = mapped_column(String(40), primary_key=True)

    # Lista ordenada de columnas visibles. Guardar el orden y no solo cuáles
    # permite que cada quien las acomode como las usa.
    columnas: Mapped[list[str]] = mapped_column(JSONB)

    updated_at: Mapped[datetime] = mapped_column(server_default=text("now()"))

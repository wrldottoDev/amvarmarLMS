from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, LargeBinary, String, Text, text
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, UUIDPk


class ClientType(StrEnum):
    WEB = "WEB"
    IOS = "IOS"
    ANDROID = "ANDROID"


class RevokeReason(StrEnum):
    LOGOUT = "LOGOUT"
    LOGOUT_ALL = "LOGOUT_ALL"
    REUSE_DETECTED = "REUSE_DETECTED"
    # S105 lo marca por el nombre; es un motivo de revocación, no una contraseña.
    PASSWORD_CHANGED = "PASSWORD_CHANGED"  # noqa: S105
    ADMIN_REVOKED = "ADMIN_REVOKED"


class AuthSession(Base):
    """Sesión de autenticación. Su `id` es el claim `sid` de los tokens.

    Es la unidad de revocación: matar la sesión invalida el refresh de inmediato
    y el access en cuanto expire (10 minutos como máximo).
    """

    __tablename__ = "auth_sessions"

    id: Mapped[UUIDPk]
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    client_type: Mapped[str] = mapped_column(String(20))
    device_name: Mapped[str | None] = mapped_column(String(150))
    ip_created: Mapped[str | None] = mapped_column(INET)
    ip_last_used: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    last_used_at: Mapped[datetime] = mapped_column(server_default=text("now()"))

    # Dos vencimientos distintos: `idle` se renueva en cada rotación, `absolute`
    # no. Una sesión activa sin parar igual caduca y obliga a reautenticarse.
    idle_expires_at: Mapped[datetime]
    absolute_expires_at: Mapped[datetime]

    revoked_at: Mapped[datetime | None]
    revoke_reason: Mapped[str | None] = mapped_column(String(80))

    __table_args__ = (
        CheckConstraint("client_type IN ('WEB', 'IOS', 'ANDROID')", name="client_type_valido"),
        Index("ix_auth_sessions_user_id_revoked_at", "user_id", "revoked_at"),
        Index("ix_auth_sessions_idle_expires_at", "idle_expires_at"),
        Index("ix_auth_sessions_absolute_expires_at", "absolute_expires_at"),
    )


class RefreshToken(Base):
    """Un refresh emitido. Su `id` es el claim `jti` del token.

    El token completo NUNCA se guarda: solo el fingerprint HMAC-SHA-256. Quien
    lea la base no puede reconstruir un token utilizable.

    La cadena `parent_token_id` → `replaced_by_token_id` deja el linaje completo
    de rotaciones, que es lo que permite investigar un incidente de reutilización.
    """

    __tablename__ = "refresh_tokens"

    id: Mapped[UUIDPk]
    session_id: Mapped[UUID] = mapped_column(ForeignKey("auth_sessions.id", ondelete="CASCADE"))

    token_hash: Mapped[bytes] = mapped_column(LargeBinary, unique=True)

    parent_token_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("refresh_tokens.id", ondelete="RESTRICT")
    )
    replaced_by_token_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("refresh_tokens.id", ondelete="RESTRICT")
    )

    issued_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]

    __table_args__ = (Index("ix_refresh_tokens_session_id", "session_id"),)


class OneTimeTokenPurpose(StrEnum):
    INVITATION = "INVITATION"
    EMAIL_VERIFY = "EMAIL_VERIFY"
    PASSWORD_RESET = "PASSWORD_RESET"  # noqa: S105


class OneTimeToken(Base):
    """Token de un solo uso: invitación, verificación de correo, reset.

    Igual que el refresh, se guarda el fingerprint HMAC y nunca el token. Quien
    lea la base no puede fabricar un enlace de recuperación válido.
    """

    __tablename__ = "one_time_tokens"

    id: Mapped[UUIDPk]
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    purpose: Mapped[str] = mapped_column(String(32))
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, unique=True)

    expires_at: Mapped[datetime]
    consumed_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))

    __table_args__ = (
        CheckConstraint(
            "purpose IN ('INVITATION', 'EMAIL_VERIFY', 'PASSWORD_RESET')",
            name="purpose_valido",
        ),
        Index("ix_one_time_tokens_user_purpose", "user_id", "purpose"),
    )

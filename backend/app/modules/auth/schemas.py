"""Esquemas de entrada y salida de /auth.

Ninguna respuesta expone `password_hash`, fingerprints ni `token_hash`: los
esquemas se construyen campo por campo, nunca serializando la fila completa.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

from app.modules.auth.models import ClientType


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=1024)
    client_type: ClientType = ClientType.WEB
    device_name: str | None = Field(default=None, max_length=150)


class TokenResponse(BaseModel):
    """El refresh NO viaja aquí: va en cookie HttpOnly.

    Devolverlo en el cuerpo lo dejaría accesible a JavaScript, que es
    exactamente lo que la cookie HttpOnly evita.
    """

    access_token: str
    # S105 lo marca por el nombre; es el esquema OAuth, no un secreto.
    token_type: str = "bearer"  # noqa: S105
    expires_at: datetime


class SessionResponse(BaseModel):
    id: UUID
    client_type: str
    device_name: str | None
    ip_last_used: str | None
    created_at: datetime
    last_used_at: datetime
    es_sesion_actual: bool


class PasswordForgotRequest(BaseModel):
    email: EmailStr


class PasswordResetRequest(BaseModel):
    token: str = Field(min_length=1, max_length=512)
    nueva_password: str = Field(min_length=12, max_length=1024)


class MensajeResponse(BaseModel):
    mensaje: str


class EmpresaResponse(BaseModel):
    id: UUID
    legal_name: str
    trade_name: str | None


class MeResponse(BaseModel):
    """Lo que el frontend usa para ADAPTAR la interfaz, nunca para autorizar.

    Que un permiso no esté en esta lista solo significa que el botón se oculta.
    El backend vuelve a verificar en cada request.
    """

    id: UUID
    email: str
    first_name: str
    last_name: str
    empresa: EmpresaResponse | None
    permisos: list[str]

"""Identificación del actor a partir del access token."""

from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.errors import NoAutenticado
from app.core.security.jwt import TokenInvalido, TokenType, decodificar
from app.modules.auth.service import sesion_activa

# auto_error=False para responder con el formato de error del proyecto en vez
# del que trae FastAPI por defecto.
_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Actor:
    user_id: UUID
    session_id: UUID


async def actor_actual(
    request: Request,
    credenciales: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
) -> Actor:
    if credenciales is None:
        raise NoAutenticado("Falta el token de acceso.")

    try:
        claims = decodificar(credenciales.credentials, TokenType.ACCESS)
    except TokenInvalido as error:
        raise NoAutenticado("Token de acceso inválido o expirado.") from error

    # Un access token es válido criptográficamente hasta que expira. Sin esta
    # consulta, un logout no tendría efecto durante los 10 minutos siguientes.
    if not await sesion_activa(session, claims.session_id):
        raise NoAutenticado("La sesión ya no está activa.")

    # Para que el log de acceso (middleware) sepa quién hizo la petición.
    request.state.actor_user_id = str(claims.user_id)

    return Actor(user_id=claims.user_id, session_id=claims.session_id)

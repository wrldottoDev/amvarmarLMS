"""Identificación del actor a partir del access token."""

from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.errors import ErrorDeAplicacion, NoAutenticado
from app.core.security.jwt import TokenInvalido, TokenType, decodificar
from app.modules.auth.service import sesion_activa

# auto_error=False para responder con el formato de error del proyecto en vez
# del que trae FastAPI por defecto.
_bearer = HTTPBearer(auto_error=False)


class DebeCambiarContrasena(ErrorDeAplicacion):
    """La cuenta arrastra una contraseña temporal y no la cambió todavía.

    Se responde 403 con un código propio para que la interfaz sepa que tiene que
    llevar a la pantalla de cambio, en vez de mostrar un error genérico.
    """

    status_code = status.HTTP_403_FORBIDDEN
    code = "DEBE_CAMBIAR_CONTRASENA"


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

    await _exigir_cambio_de_contrasena(session, request, claims.user_id)

    return Actor(user_id=claims.user_id, session_id=claims.session_id)


# Lo único que se puede hacer con una contraseña temporal: verse a uno mismo,
# cambiarla y salir. Todo lo demás espera.
_PERMITIDO_CON_CONTRASENA_TEMPORAL = frozenset(
    {
        "/api/v1/me",
        "/api/v1/me/password",
        "/api/v1/auth/logout",
        "/api/v1/auth/refresh",
    }
)


async def _exigir_cambio_de_contrasena(
    session: AsyncSession, request: Request, user_id: UUID
) -> None:
    """Bloquea el sistema hasta que la persona cambie su contraseña temporal.

    Sin esto, `must_change_password` es un campo que se guarda y nadie revisa:
    quien recibe una contraseña temporal la usa para siempre, y esa contraseña
    la conoce quien creó la cuenta. Marcar la intención sin imponerla es peor
    que no marcarla, porque parece un control que existe.

    La comprobación va acá, en la dependencia que usan todos los endpoints, y no
    en la interfaz: una pantalla se puede saltar llamando a la API directo.
    """
    if request.url.path in _PERMITIDO_CON_CONTRASENA_TEMPORAL:
        return

    debe_cambiar = (
        await session.execute(
            text("SELECT must_change_password FROM users WHERE id = :u"), {"u": user_id}
        )
    ).scalar_one_or_none()

    if debe_cambiar:
        raise DebeCambiarContrasena("Tenés que cambiar tu contraseña temporal antes de seguir.")

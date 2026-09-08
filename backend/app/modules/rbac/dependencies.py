"""Dependencia de autorización para endpoints.

Regla del proyecto: el backend valida SIEMPRE permiso y alcance. Que el frontend
esconda un botón no es control de seguridad.
"""

from collections.abc import Awaitable, Callable
from uuid import UUID

from fastapi import Depends, HTTPException, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.redis import get_redis
from app.modules.rbac.service import PermisosEfectivos, obtener_permisos_efectivos

# Resolver de empresa objetivo: dado el request, devuelve de qué empresa es el
# recurso. Se inyecta por endpoint porque cada recurso la obtiene de un lado
# distinto (path param, cuerpo, o una consulta a la base).
ResolverEmpresa = Callable[..., Awaitable[UUID | None]]


def require_permission(
    code: str,
    *,
    resolver_empresa: ResolverEmpresa | None = None,
) -> Callable[..., Awaitable[PermisosEfectivos]]:
    """Exige un permiso concreto sobre el recurso del request.

    Sin `resolver_empresa`, solo pasa quien tenga el permiso con alcance GLOBAL
    — apropiado para endpoints que no operan sobre una empresa concreta
    (`rbac.manage`, `system_settings.manage`).
    """

    async def dependencia(
        # Reemplazado en Paso 1.6 por el usuario del access token.
        user_id: UUID = Depends(_user_id_actual),
        session: AsyncSession = Depends(get_session),
        redis: Redis = Depends(get_redis),
    ) -> PermisosEfectivos:
        permisos = await obtener_permisos_efectivos(session, redis, user_id)

        company_id = await resolver_empresa() if resolver_empresa is not None else None

        if not permisos.permite(code, company_id=company_id):
            # 404, no 403: confirmar que el recurso existe pero no es tuyo ya
            # filtra información sobre otra empresa. Desde afuera, un recurso
            # ajeno es indistinguible de uno inexistente.
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Recurso no encontrado",
            )

        return permisos

    return dependencia


async def _user_id_actual() -> UUID:
    """Marcador de posición hasta el Paso 1.6 (JWT).

    Falla en vez de devolver un usuario de prueba: un endpoint que se monte
    antes de que exista la autenticación no debe quedar accesible sin dueño.
    """
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Autenticación no disponible todavía (Paso 1.6)",
    )

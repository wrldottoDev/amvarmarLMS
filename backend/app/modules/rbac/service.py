"""Cálculo de permisos efectivos, con caché en Redis.

Deny-by-default: si un permiso no aparece explícitamente en el resultado, no se
tiene. No hay comodines ni herencia implícita entre roles.
"""

import json
from dataclasses import dataclass
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.rbac.models import ScopeType

# Ventana corta a propósito. La invalidación real la da `authz_version`, que
# forma parte de la clave: al cambiar una asignación, la clave vieja deja de
# consultarse y expira sola. El TTL solo acota cuánto vive basura huérfana.
CACHE_TTL_SEGUNDOS = 300


@dataclass(frozen=True)
class PermisoEfectivo:
    """Un permiso junto con el alcance en que se otorga."""

    code: str
    scope_type: str
    company_id: UUID | None


@dataclass(frozen=True)
class PermisosEfectivos:
    user_id: UUID
    authz_version: int
    permisos: tuple[PermisoEfectivo, ...]

    def permite(
        self,
        code: str,
        *,
        company_id: UUID | None = None,
    ) -> bool:
        """¿El usuario tiene este permiso sobre este recurso?

        `company_id` es la empresa dueña del recurso que se está tocando. Un
        permiso GLOBAL alcanza cualquier empresa; uno ORGANIZATION solo la suya.
        """
        for permiso in self.permisos:
            if permiso.code != code:
                continue
            if permiso.scope_type == ScopeType.GLOBAL:
                return True
            # Sin empresa objetivo no se puede afirmar que el alcance cubra el
            # recurso: negar es la respuesta segura.
            if (
                permiso.scope_type == ScopeType.ORGANIZATION
                and company_id is not None
                and permiso.company_id == company_id
            ):
                return True
            # ASSIGNED y OWN necesitan datos del recurso (a quién está asignado,
            # quién lo creó) que esta capa no tiene. Se resuelven en la política
            # de dominio, cuando existan las tablas correspondientes (Paso 2.2).
        return False

    def codigos(self) -> set[str]:
        """Códigos otorgados, sin alcance. Para `GET /me` (Paso 1.8)."""
        return {p.code for p in self.permisos}


_CONSULTA = text("""
    SELECT p.code, ura.scope_type, ura.company_id
    FROM user_role_assignments ura
    JOIN role_permissions rp ON rp.role_id = ura.role_id
    JOIN permissions p ON p.id = rp.permission_id
    WHERE ura.user_id = :user_id
      AND (ura.expires_at IS NULL OR ura.expires_at > now())
""")


def _clave_cache(user_id: UUID, authz_version: int) -> str:
    # authz_version en la clave: cambiarla invalida la caché sin necesidad de
    # borrar nada ni de rastrear qué claves existen.
    return f"authz:{user_id}:v{authz_version}"


async def obtener_permisos_efectivos(
    session: AsyncSession,
    redis: Redis,
    user_id: UUID,
) -> PermisosEfectivos:
    authz_version = (
        await session.execute(
            text("SELECT authz_version FROM users WHERE id = :user_id AND deleted_at IS NULL"),
            {"user_id": user_id},
        )
    ).scalar_one_or_none()

    if authz_version is None:
        # Usuario inexistente o borrado: sin permisos, no un error.
        return PermisosEfectivos(user_id=user_id, authz_version=0, permisos=())

    clave = _clave_cache(user_id, authz_version)
    cacheado = await redis.get(clave)
    if cacheado is not None:
        filas = json.loads(cacheado)
        return PermisosEfectivos(
            user_id=user_id,
            authz_version=authz_version,
            permisos=tuple(
                PermisoEfectivo(
                    code=f["code"],
                    scope_type=f["scope_type"],
                    company_id=UUID(f["company_id"]) if f["company_id"] else None,
                )
                for f in filas
            ),
        )

    resultado = await session.execute(_CONSULTA, {"user_id": user_id})
    permisos = tuple(
        PermisoEfectivo(code=fila.code, scope_type=fila.scope_type, company_id=fila.company_id)
        for fila in resultado
    )

    await redis.set(
        clave,
        json.dumps(
            [
                {
                    "code": p.code,
                    "scope_type": p.scope_type,
                    "company_id": str(p.company_id) if p.company_id else None,
                }
                for p in permisos
            ]
        ),
        ex=CACHE_TTL_SEGUNDOS,
    )

    return PermisosEfectivos(user_id=user_id, authz_version=authz_version, permisos=permisos)


async def invalidar_permisos(session: AsyncSession, user_id: UUID) -> None:
    """Invalida la caché de permisos de un usuario.

    Se llama DENTRO de la misma transacción que cambia una asignación de rol.
    No borra claves de Redis: incrementa `authz_version`, con lo que la clave
    vieja deja de consultarse y expira sola. Así no hay ventana en la que la
    base ya cambió pero Redis todavía sirve permisos viejos, ni importa si
    Redis está caído en ese momento.
    """
    await session.execute(
        text("UPDATE users SET authz_version = authz_version + 1 WHERE id = :user_id"),
        {"user_id": user_id},
    )

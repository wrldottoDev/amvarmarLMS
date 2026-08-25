"""Siembra roles, permisos y sus vínculos desde el catálogo (ADR-0004).

Idempotente: correrlo N veces deja exactamente el mismo estado. Se apoya en
`ON CONFLICT` sobre los códigos, que son la identidad de negocio de cada fila.

El seed también RETIRA vínculos rol→permiso que ya no están en el catálogo. Sin
eso, quitar un permiso de un rol en ADR-0004 no tendría efecto sobre una base ya
sembrada: el rol conservaría el acceso para siempre.

Uso:
    python -m scripts.seed_rbac
"""

import asyncio
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_engine, get_sessionmaker
from app.modules.rbac.catalog import PERMISSIONS, ROLES


async def sembrar_permisos(session: AsyncSession) -> None:
    for code, description in PERMISSIONS.items():
        resource, _, action = code.partition(".")
        await session.execute(
            text("""
                INSERT INTO permissions (code, resource, action, description)
                VALUES (:code, :resource, :action, :description)
                ON CONFLICT (code) DO UPDATE
                    SET resource = EXCLUDED.resource,
                        action = EXCLUDED.action,
                        description = EXCLUDED.description
            """),
            {"code": code, "resource": resource, "action": action, "description": description},
        )


async def sembrar_roles(session: AsyncSession) -> None:
    for code, definicion in ROLES.items():
        await session.execute(
            text("""
                INSERT INTO roles (code, name, description, allowed_scopes, is_system)
                VALUES (:code, :name, :description, :allowed_scopes, true)
                ON CONFLICT (code) DO UPDATE
                    SET name = EXCLUDED.name,
                        description = EXCLUDED.description,
                        allowed_scopes = EXCLUDED.allowed_scopes,
                        updated_at = now()
            """),
            {
                "code": str(code),
                "name": definicion.name,
                "description": definicion.description,
                "allowed_scopes": [str(s) for s in definicion.allowed_scopes],
            },
        )


async def sembrar_role_permissions(session: AsyncSession) -> None:
    for code, definicion in ROLES.items():
        codigos = sorted(definicion.permissions)

        await session.execute(
            text("""
                INSERT INTO role_permissions (role_id, permission_id)
                SELECT r.id, p.id
                FROM roles r
                CROSS JOIN permissions p
                WHERE r.code = :role_code AND p.code = ANY(:permission_codes)
                ON CONFLICT (role_id, permission_id) DO NOTHING
            """),
            {"role_code": str(code), "permission_codes": codigos},
        )

        # Retirar lo que el catálogo ya no otorga.
        await session.execute(
            text("""
                DELETE FROM role_permissions rp
                USING roles r, permissions p
                WHERE rp.role_id = r.id
                  AND rp.permission_id = p.id
                  AND r.code = :role_code
                  AND NOT (p.code = ANY(:permission_codes))
            """),
            {"role_code": str(code), "permission_codes": codigos},
        )


async def sembrar(session: AsyncSession) -> dict[str, int]:
    # Un solo commit al final: si algo falla, la base queda como estaba y no a
    # medio sembrar.
    await sembrar_permisos(session)
    await sembrar_roles(session)
    await sembrar_role_permissions(session)
    await session.commit()

    return {
        "permissions": (
            await session.execute(text("SELECT count(*) FROM permissions"))
        ).scalar_one(),
        "roles": (await session.execute(text("SELECT count(*) FROM roles"))).scalar_one(),
        "role_permissions": (
            await session.execute(text("SELECT count(*) FROM role_permissions"))
        ).scalar_one(),
    }


async def main() -> None:
    async with get_sessionmaker()() as session:
        conteos = await sembrar(session)

    await get_engine().dispose()

    print("Seed RBAC completado:")
    for tabla, total in conteos.items():
        print(f"  {tabla}: {total}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(1)

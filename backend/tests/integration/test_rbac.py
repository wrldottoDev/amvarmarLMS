"""RBAC: seed idempotente, matriz rol/permiso/alcance y caché (Paso 1.4)."""

from uuid import UUID

import pytest
from scripts.seed_rbac import sembrar
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.rbac.catalog import PERMISSIONS, ROLES, Perm
from app.modules.rbac.models import RoleCode, ScopeType
from app.modules.rbac.service import invalidar_permisos, obtener_permisos_efectivos

pytestmark = pytest.mark.integration


class TestSeed:
    async def test_es_idempotente(self, session: AsyncSession) -> None:
        """Correrlo tres veces deja exactamente el mismo estado."""
        conteos = [await sembrar(session) for _ in range(3)]

        assert conteos[0] == conteos[1] == conteos[2]
        assert conteos[0]["permissions"] == len(PERMISSIONS)
        assert conteos[0]["roles"] == len(ROLES)

    async def test_siembra_los_29_permisos_del_catalogo(self, session: AsyncSession) -> None:
        await sembrar(session)

        codigos = set((await session.execute(text("SELECT code FROM permissions"))).scalars().all())
        assert codigos == set(PERMISSIONS)

    async def test_retira_permisos_que_salen_del_catalogo(self, session: AsyncSession) -> None:
        """Quitar un permiso de un rol en el catálogo debe quitarlo de la base.

        Sin esto, un rol conservaría para siempre un acceso ya revocado.
        """
        await sembrar(session)

        # Otorgar a mano un permiso que el catálogo no le da a CLIENT_USER.
        await session.execute(
            text("""
                INSERT INTO role_permissions (role_id, permission_id)
                SELECT r.id, p.id FROM roles r, permissions p
                WHERE r.code = 'CLIENT_USER' AND p.code = :code
            """),
            {"code": Perm.RBAC_MANAGE},
        )

        await sembrar(session)

        sigue = (
            await session.execute(
                text("""
                    SELECT count(*) FROM role_permissions rp
                    JOIN roles r ON r.id = rp.role_id
                    JOIN permissions p ON p.id = rp.permission_id
                    WHERE r.code = 'CLIENT_USER' AND p.code = :code
                """),
                {"code": Perm.RBAC_MANAGE},
            )
        ).scalar_one()
        assert sigue == 0


def _casos_matriz() -> list[tuple[str, str, bool]]:
    """(rol, permiso, se_otorga) para los 5 roles por 29 permisos = 145 casos."""
    return [
        (str(role_code), code, code in definicion.permissions)
        for role_code, definicion in ROLES.items()
        for code in PERMISSIONS
    ]


class TestMatrizPermisos:
    @pytest.mark.parametrize(("role_code", "permission_code", "otorgado"), _casos_matriz())
    async def test_matriz_completa(
        self,
        session: AsyncSession,
        role_code: str,
        permission_code: str,
        otorgado: bool,
    ) -> None:
        """Cada celda de la matriz de ADR-0004, verificada contra la base."""
        await sembrar(session)

        existe = (
            await session.execute(
                text("""
                    SELECT count(*) FROM role_permissions rp
                    JOIN roles r ON r.id = rp.role_id
                    JOIN permissions p ON p.id = rp.permission_id
                    WHERE r.code = :role_code AND p.code = :permission_code
                """),
                {"role_code": role_code, "permission_code": permission_code},
            )
        ).scalar_one()

        assert bool(existe) is otorgado

    async def test_super_admin_tiene_todo(self, session: AsyncSession) -> None:
        await sembrar(session)
        total = (
            await session.execute(
                text("""
                    SELECT count(*) FROM role_permissions rp
                    JOIN roles r ON r.id = rp.role_id
                    WHERE r.code = 'SUPER_ADMIN'
                """)
            )
        ).scalar_one()
        assert total == len(PERMISSIONS)

    async def test_solo_super_admin_revierte_entregas(self, session: AsyncSession) -> None:
        await sembrar(session)
        roles = (
            (
                await session.execute(
                    text("""
                        SELECT r.code FROM role_permissions rp
                        JOIN roles r ON r.id = rp.role_id
                        JOIN permissions p ON p.id = rp.permission_id
                        WHERE p.code = :code
                    """),
                    {"code": Perm.SHIPMENTS_TRANSITION_REVERT_DELIVERED},
                )
            )
            .scalars()
            .all()
        )
        assert set(roles) == {RoleCode.SUPER_ADMIN}

    async def test_agente_no_cancela_en_transito(self, session: AsyncSession) -> None:
        """ADR-0004: OPS_AGENT cancela desde PRE_ALERT, no desde IN_TRANSIT."""
        await sembrar(session)
        roles = (
            (
                await session.execute(
                    text("""
                        SELECT r.code FROM role_permissions rp
                        JOIN roles r ON r.id = rp.role_id
                        JOIN permissions p ON p.id = rp.permission_id
                        WHERE p.code = :code
                    """),
                    {"code": Perm.SHIPMENTS_CANCEL_IN_TRANSIT},
                )
            )
            .scalars()
            .all()
        )
        assert set(roles) == {RoleCode.SUPER_ADMIN, RoleCode.OPS_ADMIN}

    async def test_clientes_no_cambian_estados_operativos(self, session: AsyncSession) -> None:
        await sembrar(session)
        roles = (
            (
                await session.execute(
                    text("""
                        SELECT r.code FROM role_permissions rp
                        JOIN roles r ON r.id = rp.role_id
                        JOIN permissions p ON p.id = rp.permission_id
                        WHERE p.code = :code
                    """),
                    {"code": Perm.SHIPMENTS_TRANSITION_FORWARD},
                )
            )
            .scalars()
            .all()
        )
        assert RoleCode.CLIENT_ADMIN not in set(roles)
        assert RoleCode.CLIENT_USER not in set(roles)


class TestConstraintsAsignaciones:
    async def test_organization_exige_empresa(self, session: AsyncSession) -> None:
        await sembrar(session)
        user_id = await _crear_usuario(session, "sinempresa@amvarmar.com")

        with pytest.raises(IntegrityError):
            await session.execute(
                text("""
                    INSERT INTO user_role_assignments (user_id, role_id, scope_type, company_id)
                    SELECT :user_id, r.id, 'ORGANIZATION', NULL FROM roles r WHERE r.code='CLIENT_USER'
                """),
                {"user_id": user_id},
            )

    async def test_global_no_admite_empresa(self, session: AsyncSession) -> None:
        await sembrar(session)
        user_id = await _crear_usuario(session, "globalconempresa@amvarmar.com")
        company_id = await _crear_empresa(session, "Empresa X S.A.")

        with pytest.raises(IntegrityError):
            await session.execute(
                text("""
                    INSERT INTO user_role_assignments (user_id, role_id, scope_type, company_id)
                    SELECT :user_id, r.id, 'GLOBAL', :company_id FROM roles r WHERE r.code='OPS_ADMIN'
                """),
                {"user_id": user_id, "company_id": company_id},
            )

    async def test_no_se_duplica_asignacion_global(self, session: AsyncSession) -> None:
        """En PostgreSQL NULL no colisiona con NULL: hace falta índice parcial."""
        await sembrar(session)
        user_id = await _crear_usuario(session, "duplicado@amvarmar.com")

        for _ in range(1):
            await session.execute(
                text("""
                    INSERT INTO user_role_assignments (user_id, role_id, scope_type)
                    SELECT :user_id, r.id, 'GLOBAL' FROM roles r WHERE r.code='OPS_ADMIN'
                """),
                {"user_id": user_id},
            )

        with pytest.raises(IntegrityError):
            await session.execute(
                text("""
                    INSERT INTO user_role_assignments (user_id, role_id, scope_type)
                    SELECT :user_id, r.id, 'GLOBAL' FROM roles r WHERE r.code='OPS_ADMIN'
                """),
                {"user_id": user_id},
            )

    async def test_scope_invalido_rechazado(self, session: AsyncSession) -> None:
        await sembrar(session)
        user_id = await _crear_usuario(session, "scopemalo@amvarmar.com")

        with pytest.raises(IntegrityError):
            await session.execute(
                text("""
                    INSERT INTO user_role_assignments (user_id, role_id, scope_type)
                    SELECT :user_id, r.id, 'TODOPODEROSO' FROM roles r WHERE r.code='OPS_ADMIN'
                """),
                {"user_id": user_id},
            )


async def _crear_usuario(session: AsyncSession, email: str) -> str:
    return str(
        (
            await session.execute(
                text("""
                    INSERT INTO users (email, password_hash, first_name, last_name, status)
                    VALUES (:email, 'hash', 'N', 'A', 'ACTIVE') RETURNING id
                """),
                {"email": email},
            )
        ).scalar_one()
    )


async def _crear_empresa(session: AsyncSession, legal_name: str) -> str:
    return str(
        (
            await session.execute(
                text("""
                    INSERT INTO companies (legal_name, status)
                    VALUES (:legal_name, 'ACTIVE') RETURNING id
                """),
                {"legal_name": legal_name},
            )
        ).scalar_one()
    )


async def _asignar_rol(
    session: AsyncSession,
    user_id: str,
    role_code: str,
    scope_type: str,
    company_id: str | None = None,
) -> None:
    await session.execute(
        text("""
            INSERT INTO user_role_assignments (user_id, role_id, scope_type, company_id)
            SELECT :user_id, r.id, :scope_type, :company_id
            FROM roles r WHERE r.code = :role_code
        """),
        {
            "user_id": user_id,
            "role_code": role_code,
            "scope_type": scope_type,
            "company_id": company_id,
        },
    )


class TestPermisosEfectivos:
    async def test_cliente_no_ve_recursos_de_otra_empresa(
        self, session: AsyncSession, redis
    ) -> None:
        """La regla de seguridad central: aislamiento por empresa.

        Un CLIENT_USER de la empresa A no tiene permiso sobre un recurso de la
        empresa B, aunque tenga el permiso `shipments.read`.
        """
        await sembrar(session)
        user_id = await _crear_usuario(session, "cliente.a@amvarmar.com")
        empresa_a = await _crear_empresa(session, "Empresa A S.A.")
        empresa_b = await _crear_empresa(session, "Empresa B S.A.")
        await _asignar_rol(
            session, user_id, RoleCode.CLIENT_USER, ScopeType.ORGANIZATION, empresa_a
        )

        permisos = await obtener_permisos_efectivos(session, redis, UUID(user_id))

        assert permisos.permite(Perm.SHIPMENTS_READ, company_id=UUID(empresa_a)) is True
        assert permisos.permite(Perm.SHIPMENTS_READ, company_id=UUID(empresa_b)) is False

    async def test_staff_global_alcanza_cualquier_empresa(
        self, session: AsyncSession, redis
    ) -> None:
        await sembrar(session)
        user_id = await _crear_usuario(session, "ops@amvarmar.com")
        empresa = await _crear_empresa(session, "Cualquiera S.A.")
        await _asignar_rol(session, user_id, RoleCode.OPS_ADMIN, ScopeType.GLOBAL)

        permisos = await obtener_permisos_efectivos(session, redis, UUID(user_id))

        assert permisos.permite(Perm.SHIPMENTS_READ, company_id=UUID(empresa)) is True

    async def test_deny_by_default(self, session: AsyncSession, redis) -> None:
        """Usuario sin rol asignado no tiene ningún permiso."""
        await sembrar(session)
        user_id = await _crear_usuario(session, "sinrol@amvarmar.com")

        permisos = await obtener_permisos_efectivos(session, redis, UUID(user_id))

        assert permisos.codigos() == set()
        assert permisos.permite(Perm.SHIPMENTS_READ) is False

    async def test_cliente_no_tiene_permiso_que_su_rol_no_otorga(
        self, session: AsyncSession, redis
    ) -> None:
        await sembrar(session)
        user_id = await _crear_usuario(session, "cliente.limitado@amvarmar.com")
        empresa = await _crear_empresa(session, "Limitada S.A.")
        await _asignar_rol(session, user_id, RoleCode.CLIENT_USER, ScopeType.ORGANIZATION, empresa)

        permisos = await obtener_permisos_efectivos(session, redis, UUID(user_id))

        assert permisos.permite(Perm.RBAC_MANAGE, company_id=UUID(empresa)) is False
        assert permisos.permite(Perm.DOCUMENTS_VERIFY, company_id=UUID(empresa)) is False

    async def test_asignacion_expirada_no_otorga_permisos(
        self, session: AsyncSession, redis
    ) -> None:
        await sembrar(session)
        user_id = await _crear_usuario(session, "expirado@amvarmar.com")
        await session.execute(
            text("""
                INSERT INTO user_role_assignments (user_id, role_id, scope_type, expires_at)
                SELECT :user_id, r.id, 'GLOBAL', now() - interval '1 day'
                FROM roles r WHERE r.code = 'OPS_ADMIN'
            """),
            {"user_id": user_id},
        )

        permisos = await obtener_permisos_efectivos(session, redis, UUID(user_id))

        assert permisos.codigos() == set()

    async def test_usuario_borrado_no_tiene_permisos(self, session: AsyncSession, redis) -> None:
        await sembrar(session)
        user_id = await _crear_usuario(session, "borrado@amvarmar.com")
        await _asignar_rol(session, user_id, RoleCode.OPS_ADMIN, ScopeType.GLOBAL)
        await session.execute(
            text("UPDATE users SET deleted_at = now() WHERE id = :id"), {"id": user_id}
        )

        permisos = await obtener_permisos_efectivos(session, redis, UUID(user_id))

        assert permisos.codigos() == set()


class TestCache:
    async def test_segunda_consulta_viene_de_cache(self, session: AsyncSession, redis) -> None:
        await sembrar(session)
        user_id = await _crear_usuario(session, "cacheado@amvarmar.com")
        await _asignar_rol(session, user_id, RoleCode.OPS_ADMIN, ScopeType.GLOBAL)

        primera = await obtener_permisos_efectivos(session, redis, UUID(user_id))
        assert await redis.exists(f"authz:{user_id}:v{primera.authz_version}") == 1

        segunda = await obtener_permisos_efectivos(session, redis, UUID(user_id))
        assert segunda.codigos() == primera.codigos()

    async def test_cambiar_asignacion_invalida_la_cache(self, session: AsyncSession, redis) -> None:
        """Subir authz_version cambia la clave: la caché vieja deja de usarse.

        No se borra nada de Redis, así que la invalidación funciona aunque Redis
        esté caído en ese momento.
        """
        await sembrar(session)
        user_id = await _crear_usuario(session, "invalidar@amvarmar.com")
        empresa = await _crear_empresa(session, "Invalida S.A.")
        await _asignar_rol(session, user_id, RoleCode.CLIENT_USER, ScopeType.ORGANIZATION, empresa)

        antes = await obtener_permisos_efectivos(session, redis, UUID(user_id))
        assert antes.permite(Perm.DOCUMENTS_VERIFY, company_id=UUID(empresa)) is False

        # Ascenderlo: quitar CLIENT_USER, darle OPS_ADMIN global.
        await session.execute(
            text("DELETE FROM user_role_assignments WHERE user_id = :id"), {"id": user_id}
        )
        await _asignar_rol(session, user_id, RoleCode.OPS_ADMIN, ScopeType.GLOBAL)
        await invalidar_permisos(session, UUID(user_id))

        despues = await obtener_permisos_efectivos(session, redis, UUID(user_id))

        assert despues.authz_version == antes.authz_version + 1
        assert despues.permite(Perm.DOCUMENTS_VERIFY, company_id=UUID(empresa)) is True

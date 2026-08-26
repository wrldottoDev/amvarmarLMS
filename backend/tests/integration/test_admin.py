"""Administración de empresas y usuarios.

Es lo que el sistema viejo hacía desde el admin de Django. La diferencia que
estas pruebas vigilan: acá cada acción pasa por el control de permisos y
alcance, así que un `CLIENT_ADMIN` administra su empresa y nada más.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from scripts.seed_rbac import sembrar as sembrar_rbac
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import SinPermiso
from app.modules.admin import service
from app.modules.rbac.models import RoleCode, ScopeType
from app.modules.rbac.service import obtener_permisos_efectivos

pytestmark = pytest.mark.integration


async def _empresa(session: AsyncSession, nombre: str, cedula: str | None = None) -> uuid.UUID:
    return (
        await session.execute(
            text("""
                INSERT INTO companies (legal_name, tax_id, status)
                VALUES (:n, :t, 'ACTIVE') RETURNING id
            """),
            {"n": nombre, "t": cedula},
        )
    ).scalar_one()


async def _usuario(
    session: AsyncSession, rol: str, alcance: str, empresa: uuid.UUID | None
) -> uuid.UUID:
    user_id = (
        await session.execute(
            text("""
                INSERT INTO users (email, password_hash, first_name, last_name, status)
                VALUES (:e,'h','N','A','ACTIVE') RETURNING id
            """),
            {"e": f"a-{uuid.uuid4().hex[:10]}@pruebas.amvarmar.com"},
        )
    ).scalar_one()
    await session.execute(
        text("""
            INSERT INTO user_role_assignments (user_id, role_id, scope_type, company_id)
            SELECT :u, r.id, :s, :c FROM roles r WHERE r.code = :rol
        """),
        {"u": user_id, "rol": rol, "s": alcance, "c": empresa},
    )
    if empresa is not None:
        await session.execute(
            text(
                "INSERT INTO company_memberships (company_id, user_id, status) "
                "VALUES (:c,:u,'ACTIVE')"
            ),
            {"c": empresa, "u": user_id},
        )
    return user_id


async def _permisos(session: AsyncSession, redis, user_id: uuid.UUID):
    return await obtener_permisos_efectivos(session, redis, user_id)


@pytest.fixture
async def entorno(session: AsyncSession, redis):
    await sembrar_rbac(session)
    alfa = await _empresa(session, "Alfa S.A.", "3-101-000001")
    beta = await _empresa(session, "Beta S.A.", "3-101-000002")
    return {
        "alfa": alfa,
        "beta": beta,
        "super": await _usuario(session, RoleCode.SUPER_ADMIN, ScopeType.GLOBAL, None),
        "ops": await _usuario(session, RoleCode.OPS_ADMIN, ScopeType.GLOBAL, None),
        "cliente_alfa": await _usuario(
            session, RoleCode.CLIENT_ADMIN, ScopeType.ORGANIZATION, alfa
        ),
    }


class TestEmpresas:
    async def test_crear_y_listar(self, session: AsyncSession, redis, entorno) -> None:
        permisos = await _permisos(session, redis, entorno["super"])

        nueva = await service.crear_empresa(
            session,
            legal_name="Gamma Importaciones S.A.",
            trade_name="Gamma",
            tax_id="3-101-000003",
            actor_user_id=entorno["super"],
            permisos=permisos,
        )

        empresas = await service.listar_empresas(session, permisos=permisos)
        assert nueva in {e.id for e in empresas}
        gamma = next(e for e in empresas if e.id == nueva)
        assert gamma.trade_name == "Gamma"
        assert gamma.usuarios == 0

    async def test_dos_empresas_no_comparten_cedula(
        self, session: AsyncSession, redis, entorno
    ) -> None:
        permisos = await _permisos(session, redis, entorno["super"])

        with pytest.raises(service.YaExiste):
            await service.crear_empresa(
                session,
                legal_name="Otra con la misma cédula",
                trade_name=None,
                tax_id="3-101-000001",
                actor_user_id=entorno["super"],
                permisos=permisos,
            )

    async def test_un_cliente_no_administra_empresas(
        self, session: AsyncSession, redis, entorno
    ) -> None:
        """`companies.manage` es de Operaciones, no del cliente."""
        permisos = await _permisos(session, redis, entorno["cliente_alfa"])

        with pytest.raises(SinPermiso):
            await service.listar_empresas(session, permisos=permisos)

    async def test_desactivar_suspende_a_sus_usuarios(
        self, session: AsyncSession, redis, entorno
    ) -> None:
        """Dejar activos a los usuarios de una empresa dada de baja les
        permitiría seguir entrando."""
        permisos = await _permisos(session, redis, entorno["super"])

        suspendidos = await service.desactivar_empresa(
            session, company_id=entorno["alfa"], permisos=permisos
        )

        assert suspendidos == 1
        estado = (
            await session.execute(
                text("SELECT status FROM users WHERE id = :u"), {"u": entorno["cliente_alfa"]}
            )
        ).scalar_one()
        assert estado == "SUSPENDED"

    async def test_desactivar_no_borra_la_empresa(
        self, session: AsyncSession, redis, entorno
    ) -> None:
        """Sus cargas y su auditoría siguen apuntando a ella."""
        permisos = await _permisos(session, redis, entorno["super"])

        await service.desactivar_empresa(session, company_id=entorno["alfa"], permisos=permisos)

        fila = (
            await session.execute(
                text("SELECT deleted_at, status FROM companies WHERE id = :c"),
                {"c": entorno["alfa"]},
            )
        ).one()
        assert fila.deleted_at is not None
        assert fila.status == "CLOSED"


class TestUsuarios:
    async def test_crear_usuario_de_cliente_con_contrasena_temporal(
        self, session: AsyncSession, redis, entorno
    ) -> None:
        permisos = await _permisos(session, redis, entorno["super"])

        creado = await service.crear_usuario(
            session,
            email="nuevo@alfa.example.com",
            first_name="Ana",
            last_name="Pérez",
            role_code=RoleCode.CLIENT_USER,
            company_id=entorno["alfa"],
            phone=None,
            permisos=permisos,
        )

        assert creado.password_temporal
        # La cuenta nace obligada a cambiarla: la temporal la conoce quien la creó.
        debe_cambiar = (
            await session.execute(
                text("SELECT must_change_password FROM users WHERE id = :u"), {"u": creado.id}
            )
        ).scalar_one()
        assert debe_cambiar is True

    async def test_el_alta_emite_una_invitacion_de_un_solo_uso(
        self, session: AsyncSession, redis, entorno
    ) -> None:
        """Reemplaza `credentials.html`, que mandaba la contraseña en texto plano.

        Se comprueba que quede el token y que la base guarde su huella y no el
        token: si guardara el token, quien pudiera leer la base entraría a
        cualquier cuenta recién creada.
        """
        permisos = await _permisos(session, redis, entorno["super"])

        creado = await service.crear_usuario(
            session,
            email="invitado@alfa.example.com",
            first_name="Rosa",
            last_name="Díaz",
            role_code=RoleCode.CLIENT_USER,
            company_id=entorno["alfa"],
            phone=None,
            permisos=permisos,
        )

        fila = (
            await session.execute(
                text("""
                    SELECT purpose, token_hash, expires_at, consumed_at
                    FROM one_time_tokens WHERE user_id = :u
                """),
                {"u": creado.id},
            )
        ).one()

        assert fila.purpose == "INVITATION"
        assert fila.consumed_at is None
        # 48 horas, con margen para el tiempo que tardó la prueba.
        assert timedelta(hours=47) < fila.expires_at - datetime.now(UTC) <= timedelta(hours=48)
        # La huella es opaca: no contiene la contraseña temporal ni el correo.
        assert creado.password_temporal.encode() not in bytes(fila.token_hash)

    async def test_el_personal_interno_no_lleva_empresa(
        self, session: AsyncSession, redis, entorno
    ) -> None:
        """ADR-0011: darle empresa lo convertiría en cliente de esa empresa."""
        permisos = await _permisos(session, redis, entorno["super"])

        with pytest.raises(service.DatosInvalidos):
            await service.crear_usuario(
                session,
                email="interno@amvarmar.example.com",
                first_name="Luis",
                last_name="Mora",
                role_code=RoleCode.OPS_AGENT,
                company_id=entorno["alfa"],
                phone=None,
                permisos=permisos,
            )

    async def test_un_cliente_no_crea_personal_interno(
        self, session: AsyncSession, redis, entorno
    ) -> None:
        """Si pudiera, se daría a sí mismo acceso a todas las empresas."""
        permisos = await _permisos(session, redis, entorno["cliente_alfa"])

        with pytest.raises(SinPermiso):
            await service.crear_usuario(
                session,
                email="colado@alfa.example.com",
                first_name="X",
                last_name="Y",
                role_code=RoleCode.OPS_ADMIN,
                company_id=None,
                phone=None,
                permisos=permisos,
            )

    async def test_un_cliente_no_crea_usuarios_en_otra_empresa(
        self, session: AsyncSession, redis, entorno
    ) -> None:
        permisos = await _permisos(session, redis, entorno["cliente_alfa"])

        with pytest.raises(SinPermiso):
            await service.crear_usuario(
                session,
                email="colado@beta.example.com",
                first_name="X",
                last_name="Y",
                role_code=RoleCode.CLIENT_USER,
                company_id=entorno["beta"],
                phone=None,
                permisos=permisos,
            )

    async def test_un_cliente_si_crea_usuarios_en_la_suya(
        self, session: AsyncSession, redis, entorno
    ) -> None:
        permisos = await _permisos(session, redis, entorno["cliente_alfa"])

        creado = await service.crear_usuario(
            session,
            email="companero@alfa.example.com",
            first_name="Marta",
            last_name="Solís",
            role_code=RoleCode.CLIENT_USER,
            company_id=entorno["alfa"],
            phone=None,
            permisos=permisos,
        )

        assert creado.id

    async def test_un_cliente_solo_ve_los_usuarios_de_su_empresa(
        self, session: AsyncSession, redis, entorno
    ) -> None:
        await _usuario(session, RoleCode.CLIENT_USER, ScopeType.ORGANIZATION, entorno["beta"])
        permisos = await _permisos(session, redis, entorno["cliente_alfa"])

        usuarios = await service.listar_usuarios(session, permisos=permisos)

        assert usuarios
        assert {u.company_id for u in usuarios} == {entorno["alfa"]}

    async def test_dos_cuentas_no_comparten_correo(
        self, session: AsyncSession, redis, entorno
    ) -> None:
        permisos = await _permisos(session, redis, entorno["super"])
        datos = {
            "email": "repetido@alfa.example.com",
            "first_name": "A",
            "last_name": "B",
            "role_code": RoleCode.CLIENT_USER,
            "company_id": entorno["alfa"],
            "phone": None,
            "permisos": permisos,
        }
        await service.crear_usuario(session, **datos)

        with pytest.raises(service.YaExiste):
            await service.crear_usuario(session, **datos)

    async def test_restablecer_contrasena_corta_las_sesiones(
        self, session: AsyncSession, redis, entorno
    ) -> None:
        """Si se restablece por sospecha de robo, dejar viva la sesión del
        atacante no arregla nada."""
        permisos = await _permisos(session, redis, entorno["super"])
        await session.execute(
            text("""
                INSERT INTO auth_sessions
                    (user_id, client_type, ip_created, idle_expires_at, absolute_expires_at)
                VALUES (:u, 'WEB', '127.0.0.1',
                        now() + interval '14 days', now() + interval '30 days')
            """),
            {"u": entorno["cliente_alfa"]},
        )

        temporal = await service.restablecer_contrasena(
            session, user_id=entorno["cliente_alfa"], permisos=permisos
        )

        assert temporal
        activas = (
            await session.execute(
                text("""
                    SELECT count(*) FROM auth_sessions
                    WHERE user_id = :u AND revoked_at IS NULL
                """),
                {"u": entorno["cliente_alfa"]},
            )
        ).scalar_one()
        assert activas == 0

    async def test_desactivar_usuario_no_lo_borra(
        self, session: AsyncSession, redis, entorno
    ) -> None:
        permisos = await _permisos(session, redis, entorno["super"])

        await service.desactivar_usuario(
            session, user_id=entorno["cliente_alfa"], permisos=permisos
        )

        fila = (
            await session.execute(
                text("SELECT status, deleted_at FROM users WHERE id = :u"),
                {"u": entorno["cliente_alfa"]},
            )
        ).one()
        assert fila.status == "SUSPENDED"
        assert fila.deleted_at is not None

    async def test_cambiar_de_rol_sube_la_version_de_permisos(
        self, session: AsyncSession, redis, entorno
    ) -> None:
        """Sin eso, el rol nuevo tardaría en aplicarse lo que dure la caché."""
        permisos = await _permisos(session, redis, entorno["super"])
        antes = (
            await session.execute(
                text("SELECT authz_version FROM users WHERE id = :u"),
                {"u": entorno["cliente_alfa"]},
            )
        ).scalar_one()

        await service.actualizar_usuario(
            session,
            user_id=entorno["cliente_alfa"],
            cambios={"role_code": RoleCode.CLIENT_USER},
            permisos=permisos,
        )

        despues = (
            await session.execute(
                text("SELECT authz_version FROM users WHERE id = :u"),
                {"u": entorno["cliente_alfa"]},
            )
        ).scalar_one()
        assert despues > antes

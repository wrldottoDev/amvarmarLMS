"""Cambio de contraseña propia y bloqueo por contraseña temporal.

`must_change_password` existía como campo desde el principio y nadie lo
revisaba: quien recibía una temporal la usaba para siempre, y esa contraseña la
conoce quien creó la cuenta. Marcar la intención sin imponerla es peor que no
marcarla, porque parece un control que existe.
"""

import uuid

import httpx
import pytest
from scripts.seed_rbac import sembrar as sembrar_rbac
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security.argon2 import hash_password

pytestmark = pytest.mark.integration

ACTUAL = "Contrasena-Actual-Larga-1"
NUEVA = "Contrasena-Nueva-Larga-2"


@pytest.fixture
async def cuenta(db_directa: AsyncSession):
    await sembrar_rbac(db_directa)
    email = f"cambio-{uuid.uuid4().hex[:8]}@pruebas.amvarmar.com"
    user_id = (
        await db_directa.execute(
            text("""
                INSERT INTO users
                    (email, password_hash, first_name, last_name, status, must_change_password)
                VALUES (:e, :h, 'N', 'A', 'ACTIVE', false) RETURNING id
            """),
            {"e": email, "h": hash_password(ACTUAL)},
        )
    ).scalar_one()
    await db_directa.execute(
        text("""
            INSERT INTO user_role_assignments (user_id, role_id, scope_type, company_id)
            SELECT :u, r.id, 'GLOBAL', NULL FROM roles r WHERE r.code = 'OPS_ADMIN'
        """),
        {"u": user_id},
    )
    await db_directa.commit()

    yield {"id": user_id, "email": email}

    await db_directa.execute(
        text("DELETE FROM user_role_assignments WHERE user_id = :u"), {"u": user_id}
    )
    await db_directa.execute(text("DELETE FROM users WHERE id = :u"), {"u": user_id})
    await db_directa.commit()


async def _entrar(cliente: httpx.AsyncClient, email: str, password: str) -> str:
    respuesta = await cliente.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    return respuesta.json().get("access_token", "")


class TestCambioPropio:
    async def test_cambia_la_contrasena(self, cliente: httpx.AsyncClient, cuenta: dict) -> None:
        token = await _entrar(cliente, cuenta["email"], ACTUAL)

        r = await cliente.post(
            "/api/v1/me/password",
            headers={"Authorization": f"Bearer {token}"},
            json={"actual": ACTUAL, "nueva": NUEVA},
        )

        assert r.status_code == 200
        assert await _entrar(cliente, cuenta["email"], NUEVA)

    async def test_exige_la_contrasena_actual(
        self, cliente: httpx.AsyncClient, cuenta: dict
    ) -> None:
        """Si alguien deja el computador abierto, sin este paso puede quedarse
        con la cuenta."""
        token = await _entrar(cliente, cuenta["email"], ACTUAL)

        r = await cliente.post(
            "/api/v1/me/password",
            headers={"Authorization": f"Bearer {token}"},
            json={"actual": "la-que-sea-larga", "nueva": NUEVA},
        )

        assert r.status_code == 401
        # Y la contraseña vieja sigue sirviendo.
        assert await _entrar(cliente, cuenta["email"], ACTUAL)

    async def test_no_acepta_la_misma_contrasena(
        self, cliente: httpx.AsyncClient, cuenta: dict
    ) -> None:
        token = await _entrar(cliente, cuenta["email"], ACTUAL)

        r = await cliente.post(
            "/api/v1/me/password",
            headers={"Authorization": f"Bearer {token}"},
            json={"actual": ACTUAL, "nueva": ACTUAL},
        )

        assert r.status_code == 422
        assert r.json()["error"]["code"] == "CONTRASENA_REPETIDA"

    async def test_la_sesion_que_cambia_sobrevive(
        self, cliente: httpx.AsyncClient, cuenta: dict
    ) -> None:
        """Cortarla obligaría a entrar de nuevo sin ninguna razón."""
        token = await _entrar(cliente, cuenta["email"], ACTUAL)

        await cliente.post(
            "/api/v1/me/password",
            headers={"Authorization": f"Bearer {token}"},
            json={"actual": ACTUAL, "nueva": NUEVA},
        )

        r = await cliente.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200

    async def test_corta_las_demas_sesiones(
        self, cliente: httpx.AsyncClient, cuenta: dict, db_directa: AsyncSession
    ) -> None:
        """Si se cambió por sospecha de robo, la del atacante tiene que caer."""
        otra = await _entrar(cliente, cuenta["email"], ACTUAL)
        actual = await _entrar(cliente, cuenta["email"], ACTUAL)

        await cliente.post(
            "/api/v1/me/password",
            headers={"Authorization": f"Bearer {actual}"},
            json={"actual": ACTUAL, "nueva": NUEVA},
        )

        activas = (
            await db_directa.execute(
                text("""
                    SELECT count(*) FROM auth_sessions
                    WHERE user_id = :u AND revoked_at IS NULL
                """),
                {"u": cuenta["id"]},
            )
        ).scalar_one()
        assert activas == 1
        assert otra


class TestContrasenaTemporal:
    """El bloqueo que antes no existía."""

    async def _marcar_temporal(self, db_directa: AsyncSession, user_id: uuid.UUID) -> None:
        await db_directa.execute(
            text("UPDATE users SET must_change_password = true WHERE id = :u"), {"u": user_id}
        )
        await db_directa.commit()

    async def test_no_deja_usar_el_sistema(
        self, cliente: httpx.AsyncClient, cuenta: dict, db_directa: AsyncSession
    ) -> None:
        await self._marcar_temporal(db_directa, cuenta["id"])
        token = await _entrar(cliente, cuenta["email"], ACTUAL)

        r = await cliente.get("/api/v1/shipments", headers={"Authorization": f"Bearer {token}"})

        assert r.status_code == 403
        # Código propio para que la interfaz sepa a dónde llevar a la persona,
        # en vez de mostrar un error genérico.
        assert r.json()["error"]["code"] == "DEBE_CAMBIAR_CONTRASENA"

    async def test_si_deja_ver_su_propia_cuenta_y_cambiarla(
        self, cliente: httpx.AsyncClient, cuenta: dict, db_directa: AsyncSession
    ) -> None:
        """Si bloqueara todo, incluido el cambio, la cuenta quedaría inservible."""
        await self._marcar_temporal(db_directa, cuenta["id"])
        token = await _entrar(cliente, cuenta["email"], ACTUAL)
        cabeceras = {"Authorization": f"Bearer {token}"}

        assert (await cliente.get("/api/v1/me", headers=cabeceras)).status_code == 200

        cambio = await cliente.post(
            "/api/v1/me/password", headers=cabeceras, json={"actual": ACTUAL, "nueva": NUEVA}
        )
        assert cambio.status_code == 200

    async def test_tras_cambiarla_se_desbloquea(
        self, cliente: httpx.AsyncClient, cuenta: dict, db_directa: AsyncSession
    ) -> None:
        await self._marcar_temporal(db_directa, cuenta["id"])
        token = await _entrar(cliente, cuenta["email"], ACTUAL)
        cabeceras = {"Authorization": f"Bearer {token}"}

        await cliente.post(
            "/api/v1/me/password", headers=cabeceras, json={"actual": ACTUAL, "nueva": NUEVA}
        )

        r = await cliente.get("/api/v1/shipments", headers=cabeceras)
        assert r.status_code == 200

    async def test_una_cuenta_creada_por_un_administrador_nace_bloqueada(
        self, cliente: httpx.AsyncClient, db_directa: AsyncSession
    ) -> None:
        """La contraseña temporal la conoce quien creó la cuenta."""
        await sembrar_rbac(db_directa)
        marca = uuid.uuid4().hex[:8]
        empresa = (
            await db_directa.execute(
                text(
                    "INSERT INTO companies (legal_name, status) VALUES (:n,'ACTIVE') RETURNING id"
                ),
                {"n": f"Temp {marca} S.A."},
            )
        ).scalar_one()
        await db_directa.commit()

        from app.modules.admin import service as admin
        from app.modules.rbac.models import RoleCode, ScopeType

        creador = (
            await db_directa.execute(
                text("""
                    INSERT INTO users (email, password_hash, first_name, last_name, status)
                    VALUES (:e, :h, 'A', 'B', 'ACTIVE') RETURNING id
                """),
                {"e": f"admin-{marca}@pruebas.amvarmar.com", "h": hash_password(ACTUAL)},
            )
        ).scalar_one()
        await db_directa.execute(
            text("""
                INSERT INTO user_role_assignments (user_id, role_id, scope_type, company_id)
                SELECT :u, r.id, :s, NULL FROM roles r WHERE r.code = :rol
            """),
            {"u": creador, "rol": RoleCode.SUPER_ADMIN.value, "s": ScopeType.GLOBAL.value},
        )
        await db_directa.commit()

        from app.modules.rbac.service import PermisoEfectivo, PermisosEfectivos

        permisos = PermisosEfectivos(
            user_id=creador,
            authz_version=1,
            permisos=tuple(
                PermisoEfectivo(code=c, scope_type=ScopeType.GLOBAL, company_id=None)
                for c in ("users.manage", "users.create.internal")
            ),
        )

        creado = await admin.crear_usuario(
            db_directa,
            email=f"nuevo-{marca}@pruebas.amvarmar.com",
            first_name="Nuevo",
            last_name="Usuario",
            role_code=RoleCode.CLIENT_USER.value,
            company_id=empresa,
            phone=None,
            permisos=permisos,
        )
        await db_directa.commit()

        token = await _entrar(cliente, creado.email, creado.password_temporal)
        r = await cliente.get("/api/v1/shipments", headers={"Authorization": f"Bearer {token}"})

        assert r.status_code == 403
        assert r.json()["error"]["code"] == "DEBE_CAMBIAR_CONTRASENA"

        await db_directa.execute(
            text("DELETE FROM user_role_assignments WHERE user_id = ANY(:u)"),
            {"u": [creador, creado.id]},
        )
        await db_directa.execute(
            text("DELETE FROM company_memberships WHERE company_id = :c"), {"c": empresa}
        )
        await db_directa.execute(
            text("DELETE FROM users WHERE id = ANY(:u)"), {"u": [creador, creado.id]}
        )
        await db_directa.execute(text("DELETE FROM companies WHERE id = :c"), {"c": empresa})
        await db_directa.commit()

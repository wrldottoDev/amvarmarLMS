"""Endpoints de /auth de punta a punta (Paso 1.8).

Contra la app real por HTTP: cubren cookies, headers, códigos de estado y el
formato de error, que los tests de servicio no ven.
"""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security.argon2 import hash_password
from app.modules.auth.router import COOKIE_REFRESH, RUTA_REFRESH

pytestmark = pytest.mark.integration

PASSWORD = "una passphrase de prueba suficientemente larga"


async def _sembrar_usuario(db: AsyncSession, email: str, *, status: str = "ACTIVE") -> str:
    user_id = (
        await db.execute(
            text("""
                INSERT INTO users (email, password_hash, first_name, last_name, status)
                VALUES (:email, :hash, 'Ana', 'Prueba', :status)
                RETURNING id
            """),
            {"email": email, "hash": hash_password(PASSWORD), "status": status},
        )
    ).scalar_one()
    await db.commit()
    return str(user_id)


async def _limpiar(db: AsyncSession, email: str) -> None:
    await db.execute(text("DELETE FROM users WHERE email = :email"), {"email": email})
    await db.commit()


@pytest.fixture
async def usuario(db_directa: AsyncSession):
    email = f"e2e-{uuid.uuid4().hex[:8]}@pruebas.amvarmar.com"
    user_id = await _sembrar_usuario(db_directa, email)
    yield email, user_id
    await _limpiar(db_directa, email)


class TestLogin:
    async def test_login_devuelve_access_y_cookie(
        self, cliente: AsyncClient, usuario: tuple[str, str]
    ) -> None:
        email, _ = usuario

        r = await cliente.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})

        assert r.status_code == 200
        assert r.json()["access_token"]
        assert r.json()["token_type"] == "bearer"
        assert COOKIE_REFRESH in r.cookies

    async def test_la_cookie_tiene_los_flags_de_seguridad(
        self, cliente: AsyncClient, usuario: tuple[str, str]
    ) -> None:
        """HttpOnly contra XSS, SameSite=Strict contra CSRF, Path acotado."""
        email, _ = usuario

        r = await cliente.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})

        cabecera = r.headers["set-cookie"].lower()
        assert "httponly" in cabecera
        assert "samesite=strict" in cabecera
        assert f"path={RUTA_REFRESH}".lower() in cabecera

    async def test_la_respuesta_no_filtra_datos_sensibles(
        self, cliente: AsyncClient, usuario: tuple[str, str]
    ) -> None:
        email, _ = usuario

        r = await cliente.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})

        cuerpo = r.text.lower()
        assert "password_hash" not in cuerpo
        assert "argon2" not in cuerpo
        assert "token_hash" not in cuerpo
        # El refresh va en cookie, nunca en el cuerpo.
        assert "refresh_token" not in cuerpo

    async def test_credenciales_malas_dan_401_con_formato_estandar(
        self, cliente: AsyncClient, usuario: tuple[str, str]
    ) -> None:
        email, _ = usuario

        r = await cliente.post(
            "/api/v1/auth/login", json={"email": email, "password": "incorrecta"}
        )

        assert r.status_code == 401
        error = r.json()["error"]
        assert error["code"] == "NO_AUTENTICADO"
        assert error["request_id"]
        assert isinstance(error["details"], list)

    async def test_usuario_inexistente_da_el_mismo_error(self, cliente: AsyncClient) -> None:
        """No se puede distinguir cuenta inexistente de contraseña mala."""
        r = await cliente.post(
            "/api/v1/auth/login",
            json={"email": "nadie@pruebas.amvarmar.com", "password": PASSWORD},
        )

        assert r.status_code == 401
        assert r.json()["error"]["code"] == "NO_AUTENTICADO"

    async def test_payload_invalido_da_422_con_detalles(self, cliente: AsyncClient) -> None:
        r = await cliente.post("/api/v1/auth/login", json={"email": "no-es-un-correo"})

        assert r.status_code == 422
        error = r.json()["error"]
        assert error["code"] == "PAYLOAD_INVALIDO"
        assert len(error["details"]) >= 1


class TestRateLimit:
    async def test_demasiados_intentos_dan_429_con_retry_after(
        self, cliente: AsyncClient, usuario: tuple[str, str]
    ) -> None:
        email, _ = usuario

        for _ in range(5):
            await cliente.post("/api/v1/auth/login", json={"email": email, "password": "mal"})

        r = await cliente.post("/api/v1/auth/login", json={"email": email, "password": "mal"})

        assert r.status_code == 429
        assert r.json()["error"]["code"] == "DEMASIADAS_SOLICITUDES"
        assert int(r.headers["retry-after"]) > 0


class TestRefresh:
    async def test_refresh_rota_la_cookie(
        self, cliente: AsyncClient, usuario: tuple[str, str]
    ) -> None:
        email, _ = usuario
        login = await cliente.post(
            "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
        )
        cookie_original = login.cookies[COOKIE_REFRESH]

        r = await cliente.post("/api/v1/auth/refresh")

        assert r.status_code == 200
        assert r.cookies[COOKIE_REFRESH] != cookie_original

    async def test_sin_cookie_da_401(self, cliente: AsyncClient) -> None:
        r = await cliente.post("/api/v1/auth/refresh")

        assert r.status_code == 401

    async def test_reutilizar_la_cookie_vieja_cierra_la_sesion(
        self, cliente: AsyncClient, usuario: tuple[str, str]
    ) -> None:
        email, _ = usuario
        login = await cliente.post(
            "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
        )
        cookie_vieja = login.cookies[COOKIE_REFRESH]
        await cliente.post("/api/v1/auth/refresh")

        # Reponer la cookie vieja en el cliente: es lo que haría un atacante que
        # capturó un refresh anterior.
        cliente.cookies.set(COOKIE_REFRESH, cookie_vieja, path=RUTA_REFRESH)
        r = await cliente.post("/api/v1/auth/refresh")

        assert r.status_code == 401
        assert r.json()["error"]["code"] == "NO_AUTENTICADO"


class TestMeYSesiones:
    async def test_me_devuelve_usuario_y_permisos(
        self, cliente: AsyncClient, usuario: tuple[str, str]
    ) -> None:
        email, user_id = usuario
        login = await cliente.post(
            "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
        )
        token = login.json()["access_token"]

        r = await cliente.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})

        assert r.status_code == 200
        cuerpo = r.json()
        assert cuerpo["id"] == user_id
        assert cuerpo["email"].lower() == email.lower()
        # Sin rol asignado: deny-by-default.
        assert cuerpo["permisos"] == []
        assert "password_hash" not in r.text

    async def test_me_sin_token_da_401(self, cliente: AsyncClient) -> None:
        r = await cliente.get("/api/v1/me")

        assert r.status_code == 401
        assert r.json()["error"]["code"] == "NO_AUTENTICADO"

    async def test_me_con_token_basura_da_401(self, cliente: AsyncClient) -> None:
        r = await cliente.get("/api/v1/me", headers={"Authorization": "Bearer no-es-un-token"})

        assert r.status_code == 401

    async def test_listar_sesiones_marca_la_actual(
        self, cliente: AsyncClient, usuario: tuple[str, str]
    ) -> None:
        email, _ = usuario
        login = await cliente.post(
            "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
        )
        token = login.json()["access_token"]

        r = await cliente.get("/api/v1/auth/sessions", headers={"Authorization": f"Bearer {token}"})

        assert r.status_code == 200
        sesiones = r.json()
        assert len(sesiones) == 1
        assert sesiones[0]["es_sesion_actual"] is True

    async def test_no_se_puede_revocar_la_sesion_de_otro(
        self, cliente: AsyncClient, usuario: tuple[str, str], db_directa: AsyncSession
    ) -> None:
        """404, no 403: confirmar que existe ya es información sobre esa persona."""
        email, _ = usuario
        otro_email = f"otro-{uuid.uuid4().hex[:8]}@pruebas.amvarmar.com"
        await _sembrar_usuario(db_directa, otro_email)
        try:
            login_otro = await cliente.post(
                "/api/v1/auth/login", json={"email": otro_email, "password": PASSWORD}
            )
            sesion_ajena = (
                await cliente.get(
                    "/api/v1/auth/sessions",
                    headers={"Authorization": f"Bearer {login_otro.json()['access_token']}"},
                )
            ).json()[0]["id"]

            login = await cliente.post(
                "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
            )
            r = await cliente.delete(
                f"/api/v1/auth/sessions/{sesion_ajena}",
                headers={"Authorization": f"Bearer {login.json()['access_token']}"},
            )

            assert r.status_code == 404
            assert r.json()["error"]["code"] == "RECURSO_NO_ENCONTRADO"
        finally:
            await _limpiar(db_directa, otro_email)


class TestLogout:
    async def test_logout_invalida_el_access_token(
        self, cliente: AsyncClient, usuario: tuple[str, str]
    ) -> None:
        """El access sigue siendo válido criptográficamente: lo que lo corta es
        que la sesión ya no existe."""
        email, _ = usuario
        login = await cliente.post(
            "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
        )
        cabeceras = {"Authorization": f"Bearer {login.json()['access_token']}"}

        assert (await cliente.post("/api/v1/auth/logout", headers=cabeceras)).status_code == 200

        assert (await cliente.get("/api/v1/me", headers=cabeceras)).status_code == 401

    async def test_logout_all_cierra_todas(
        self, cliente: AsyncClient, usuario: tuple[str, str]
    ) -> None:
        email, _ = usuario
        primera = await cliente.post(
            "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
        )
        segunda = await cliente.post(
            "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
        )

        r = await cliente.post(
            "/api/v1/auth/logout-all",
            headers={"Authorization": f"Bearer {segunda.json()['access_token']}"},
        )

        assert r.status_code == 200
        for respuesta in (primera, segunda):
            token = respuesta.json()["access_token"]
            assert (
                await cliente.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
            ).status_code == 401


class TestRecuperacionDePassword:
    async def test_forgot_responde_igual_exista_o_no_la_cuenta(
        self, cliente: AsyncClient, usuario: tuple[str, str]
    ) -> None:
        """La respuesta no puede convertirse en un verificador de correos."""
        email, _ = usuario

        existente = await cliente.post("/api/v1/auth/password/forgot", json={"email": email})
        inexistente = await cliente.post(
            "/api/v1/auth/password/forgot",
            json={"email": f"nadie-{uuid.uuid4().hex[:8]}@pruebas.amvarmar.com"},
        )

        assert existente.status_code == inexistente.status_code == 200
        assert existente.json() == inexistente.json()

    async def test_reset_con_token_invalido_falla(self, cliente: AsyncClient) -> None:
        r = await cliente.post(
            "/api/v1/auth/password/reset",
            json={"token": "token-inventado", "nueva_password": "una nueva passphrase larga"},
        )

        assert r.status_code == 422
        assert r.json()["error"]["code"] == "TOKEN_RECUPERACION_INVALIDO"

    async def test_password_corta_se_rechaza(self, cliente: AsyncClient) -> None:
        r = await cliente.post(
            "/api/v1/auth/password/reset", json={"token": "x", "nueva_password": "corta"}
        )

        assert r.status_code == 422
        assert r.json()["error"]["code"] == "PAYLOAD_INVALIDO"


class TestRequestId:
    async def test_toda_respuesta_lleva_request_id(self, cliente: AsyncClient) -> None:
        r = await cliente.get("/health/live")

        assert uuid.UUID(r.headers["X-Request-ID"])

    async def test_se_respeta_el_request_id_del_cliente(self, cliente: AsyncClient) -> None:
        propio = str(uuid.uuid4())

        r = await cliente.get("/health/live", headers={"X-Request-ID": propio})

        assert r.headers["X-Request-ID"] == propio

    async def test_un_request_id_no_uuid_se_descarta(self, cliente: AsyncClient) -> None:
        """Aceptar cualquier string permitiría inyectar contenido en los logs."""
        r = await cliente.get("/health/live", headers={"X-Request-ID": "no-uuid\ninyectado"})

        assert uuid.UUID(r.headers["X-Request-ID"])
        assert "inyectado" not in r.headers["X-Request-ID"]

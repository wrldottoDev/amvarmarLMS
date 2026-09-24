"""Verificación de correo con `EMAIL_VERIFY` (ADR-0008).

Sin correo verificado los avisos por correo quedan SKIPPED. Este flujo es la vía
para verificarlo sin cambiar la contraseña: la persona pide un enlace desde su
cuenta y lo abre desde su buzón.
"""

import uuid
from datetime import datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.test_auth_endpoints import (
    PASSWORD,
    _invitacion,
    _limpiar,
    _sembrar_usuario,
)

pytestmark = pytest.mark.integration


@pytest.fixture
async def usuario(db_directa: AsyncSession):
    email = f"verif-{uuid.uuid4().hex[:8]}@pruebas.amvarmar.com"
    user_id = await _sembrar_usuario(db_directa, email)
    yield email, user_id
    await _limpiar(db_directa, email)


async def _verificado_en(db: AsyncSession, user_id: str) -> datetime | None:
    fecha: datetime | None = (
        await db.execute(text("SELECT email_verified_at FROM users WHERE id = :u"), {"u": user_id})
    ).scalar_one()
    return fecha


async def _tokens_vivos(db: AsyncSession, user_id: str) -> int:
    return int(
        (
            await db.execute(
                text("""
                    SELECT count(*) FROM one_time_tokens
                    WHERE user_id = :u AND purpose = 'EMAIL_VERIFY'
                      AND consumed_at IS NULL AND expires_at > now()
                """),
                {"u": user_id},
            )
        ).scalar_one()
    )


async def _verificacion(db: AsyncSession, user_id: str) -> str:
    """Token en claro, emitido por el servicio real."""
    from app.modules.auth.service import crear_token_verificacion

    token = await crear_token_verificacion(db, uuid.UUID(user_id))
    await db.commit()
    return token


async def _cabeceras(cliente: AsyncClient, email: str) -> dict[str, str]:
    r = await cliente.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


class TestPedirVerificacion:
    async def test_emite_un_enlace_para_la_cuenta_propia(
        self, cliente: AsyncClient, db_directa: AsyncSession, usuario: tuple[str, str]
    ) -> None:
        email, user_id = usuario

        r = await cliente.post(
            "/api/v1/auth/email/verify/request", headers=await _cabeceras(cliente, email)
        )

        assert r.status_code == 200
        assert await _tokens_vivos(db_directa, user_id) == 1

    async def test_sin_sesion_da_401(self, cliente: AsyncClient) -> None:
        r = await cliente.post("/api/v1/auth/email/verify/request")
        assert r.status_code == 401

    async def test_un_correo_ya_verificado_no_emite_nada(
        self, cliente: AsyncClient, db_directa: AsyncSession, usuario: tuple[str, str]
    ) -> None:
        email, user_id = usuario
        await db_directa.execute(
            text("UPDATE users SET email_verified_at = now() WHERE id = :u"), {"u": user_id}
        )
        await db_directa.commit()

        r = await cliente.post(
            "/api/v1/auth/email/verify/request", headers=await _cabeceras(cliente, email)
        )

        assert r.status_code == 200
        assert await _tokens_vivos(db_directa, user_id) == 0

    async def test_pedir_otro_invalida_el_anterior(
        self, cliente: AsyncClient, db_directa: AsyncSession, usuario: tuple[str, str]
    ) -> None:
        """Un correo viejo reenviado no puede seguir sirviendo."""
        email, user_id = usuario
        viejo = await _verificacion(db_directa, user_id)

        await cliente.post(
            "/api/v1/auth/email/verify/request", headers=await _cabeceras(cliente, email)
        )

        assert await _tokens_vivos(db_directa, user_id) == 1
        r = await cliente.post("/api/v1/auth/email/verify/confirm", json={"token": viejo})
        assert r.status_code == 422

    async def test_tiene_limite_de_pedidos(
        self, cliente: AsyncClient, usuario: tuple[str, str]
    ) -> None:
        """Cada pedido manda un correo: sin límite, la cuenta sirve para spamear el buzón."""
        email, _ = usuario
        cabeceras = await _cabeceras(cliente, email)

        estados = [
            (await cliente.post("/api/v1/auth/email/verify/request", headers=cabeceras)).status_code
            for _ in range(4)
        ]

        assert estados[:3] == [200, 200, 200]
        assert estados[3] == 429


class TestConfirmarVerificacion:
    async def test_el_enlace_verifica_y_se_quema(
        self, cliente: AsyncClient, db_directa: AsyncSession, usuario: tuple[str, str]
    ) -> None:
        _email, user_id = usuario
        token = await _verificacion(db_directa, user_id)

        primera = await cliente.post("/api/v1/auth/email/verify/confirm", json={"token": token})
        segunda = await cliente.post("/api/v1/auth/email/verify/confirm", json={"token": token})

        assert primera.status_code == 200
        assert await _verificado_en(db_directa, user_id) is not None
        assert segunda.status_code == 422
        assert segunda.json()["error"]["code"] == "TOKEN_VERIFICACION_INVALIDO"

    async def test_no_cambia_la_contrasena_ni_cierra_sesiones(
        self, cliente: AsyncClient, db_directa: AsyncSession, usuario: tuple[str, str]
    ) -> None:
        """Verificar el correo no es un cambio de credenciales."""
        email, user_id = usuario
        cabeceras = await _cabeceras(cliente, email)
        token = await _verificacion(db_directa, user_id)

        await cliente.post("/api/v1/auth/email/verify/confirm", json={"token": token})

        assert (await cliente.get("/api/v1/me", headers=cabeceras)).status_code == 200
        entrar = await cliente.post(
            "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
        )
        assert entrar.status_code == 200

    async def test_un_enlace_vencido_no_verifica(
        self, cliente: AsyncClient, db_directa: AsyncSession, usuario: tuple[str, str]
    ) -> None:
        _email, user_id = usuario
        token = await _verificacion(db_directa, user_id)
        await db_directa.execute(
            text("""
                UPDATE one_time_tokens SET expires_at = now() - interval '1 minute'
                WHERE user_id = :u AND purpose = 'EMAIL_VERIFY'
            """),
            {"u": user_id},
        )
        await db_directa.commit()

        r = await cliente.post("/api/v1/auth/email/verify/confirm", json={"token": token})

        assert r.status_code == 422
        assert await _verificado_en(db_directa, user_id) is None

    async def test_los_propositos_no_se_mezclan(
        self, cliente: AsyncClient, db_directa: AsyncSession, usuario: tuple[str, str]
    ) -> None:
        """Una invitación no verifica sin elegir contraseña, ni al revés.

        Si el de verificación sirviera como invitación, alguien con acceso a un
        enlace de 24 horas podría fijar la contraseña de la cuenta.
        """
        _email, user_id = usuario
        invitacion = await _invitacion(db_directa, user_id)
        verificacion = await _verificacion(db_directa, user_id)

        confirmar = await cliente.post(
            "/api/v1/auth/email/verify/confirm", json={"token": invitacion}
        )
        aceptar = await cliente.post(
            "/api/v1/auth/invitation/accept",
            json={
                "token": verificacion,
                "nueva_password": "una passphrase larga de prueba",  # pragma: allowlist secret
            },
        )

        assert confirmar.status_code == 422
        assert aceptar.status_code == 422
        assert await _verificado_en(db_directa, user_id) is None


class TestMe:
    async def test_me_informa_si_el_correo_esta_verificado(
        self, cliente: AsyncClient, db_directa: AsyncSession, usuario: tuple[str, str]
    ) -> None:
        email, user_id = usuario
        cabeceras = await _cabeceras(cliente, email)

        antes = (await cliente.get("/api/v1/me", headers=cabeceras)).json()
        token = await _verificacion(db_directa, user_id)
        await cliente.post("/api/v1/auth/email/verify/confirm", json={"token": token})
        despues = (await cliente.get("/api/v1/me", headers=cabeceras)).json()

        assert antes["email_verificado"] is False
        assert despues["email_verificado"] is True

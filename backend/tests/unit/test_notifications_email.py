"""`enviar()` elige el modo de TLS correcto según la configuración.

TLS implícito (puerto 465, `use_tls`) y STARTTLS (puerto 587, `start_tls`) son
mecanismos de protocolo distintos: usar el flag equivocado para el puerto
configurado no conecta. Sin este test, un typo en cuál flag se pasa a
`aiosmtplib.send` solo se nota en producción, contra el relay real.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.modules.notifications import email

pytestmark = pytest.mark.unit


def _settings(**overrides):
    base = {
        "smtp_host": "mail.amvarmar.com",
        "smtp_port": 465,
        "smtp_username": "pricing1@amvarmar.com",
        "smtp_password": "secreto",  # pragma: allowlist secret
        "smtp_use_tls": False,
        "smtp_use_ssl": False,
        "smtp_timeout_seconds": 20,
        "email_from": "AMVARMAR <pricing1@amvarmar.com>",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


async def _enviar_con(monkeypatch: pytest.MonkeyPatch, settings) -> AsyncMock:
    monkeypatch.setattr(email, "get_settings", lambda: settings)
    envio = AsyncMock()
    monkeypatch.setattr(email.aiosmtplib, "send", envio)

    correo = email.CorreoCompuesto(asunto="Prueba", texto="texto", html="<p>html</p>", enlace="x")
    await email.enviar("destino@ejemplo.com", correo)
    return envio


class TestModoDeTls:
    async def test_puerto_465_usa_tls_implicito(self, monkeypatch: pytest.MonkeyPatch) -> None:
        envio = await _enviar_con(monkeypatch, _settings(smtp_port=465, smtp_use_ssl=True))

        _, argumentos = envio.call_args
        assert argumentos["use_tls"] is True
        assert argumentos["start_tls"] is False
        assert argumentos["port"] == 465

    async def test_puerto_587_usa_starttls(self, monkeypatch: pytest.MonkeyPatch) -> None:
        envio = await _enviar_con(monkeypatch, _settings(smtp_port=587, smtp_use_tls=True))

        _, argumentos = envio.call_args
        assert argumentos["start_tls"] is True
        assert argumentos["use_tls"] is False
        assert argumentos["port"] == 587

    async def test_mailpit_local_sin_tls(self, monkeypatch: pytest.MonkeyPatch) -> None:
        envio = await _enviar_con(monkeypatch, _settings(smtp_host="localhost", smtp_port=1025))

        _, argumentos = envio.call_args
        assert argumentos["use_tls"] is False
        assert argumentos["start_tls"] is False


class TestCredencialesVacias:
    """`aiosmtplib` decide autenticar con `is not None`, no con verdadero/falso:
    un string vacío (como el que fuerza `_smtp_nunca_al_relay_real` en los
    tests) igual dispararía el login si se lo pasara tal cual, y un relay sin
    TLS que no anuncia AUTH (como el Mailpit persistente) lo rechaza."""

    async def test_username_vacio_viaja_como_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        envio = await _enviar_con(monkeypatch, _settings(smtp_username="", smtp_password=""))

        _, argumentos = envio.call_args
        assert argumentos["username"] is None
        assert argumentos["password"] is None

    async def test_credenciales_reales_viajan_tal_cual(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        envio = await _enviar_con(
            monkeypatch, _settings(smtp_username="pricing1@amvarmar.com", smtp_password="x")
        )

        _, argumentos = envio.call_args
        assert argumentos["username"] == "pricing1@amvarmar.com"
        assert argumentos["password"] == "x"

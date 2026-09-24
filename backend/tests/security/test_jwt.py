"""Validación de claims de los JWT (Paso 1.6).

Firma correcta no basta: un token emitido para otro entorno, con otra audiencia
o de otro tipo se rechaza igual que uno falsificado.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest

from app.core.config import get_settings
from app.core.security.jwt import (
    ALGORITMO,
    TokenInvalido,
    TokenType,
    decodificar,
    emitir_access_token,
    emitir_refresh_token,
)

pytestmark = pytest.mark.security


def _payload_base(**overrides: object) -> dict[str, object]:
    settings = get_settings()
    ahora = datetime.now(UTC)
    payload: dict[str, object] = {
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "sub": str(uuid4()),
        "sid": str(uuid4()),
        "jti": str(uuid4()),
        "typ": "access",
        "iat": ahora,
        "nbf": ahora,
        "exp": ahora + timedelta(minutes=10),
    }
    payload.update(overrides)
    return payload


def test_access_token_valido_se_decodifica() -> None:
    user_id, session_id = uuid4(), uuid4()

    token, expira = emitir_access_token(user_id, session_id)
    claims = decodificar(token, TokenType.ACCESS)

    assert claims.user_id == user_id
    assert claims.session_id == session_id
    assert claims.tipo is TokenType.ACCESS
    assert expira > datetime.now(UTC)


def test_access_token_dura_diez_minutos() -> None:
    _, expira = emitir_access_token(uuid4(), uuid4())

    restante = (expira - datetime.now(UTC)).total_seconds()

    assert 590 <= restante <= 600


def test_refresh_token_devuelve_su_jti() -> None:
    """El `jti` es la PK de `refresh_tokens`: sin él no se puede enlazar."""
    token, jti, expira = emitir_refresh_token(uuid4(), uuid4())

    claims = decodificar(token, TokenType.REFRESH)

    assert claims.jti == jti
    assert expira > datetime.now(UTC)


def test_un_refresh_no_sirve_como_access() -> None:
    """Sin validar `typ`, un refresh saltaría la rotación entera."""
    token, _, _ = emitir_refresh_token(uuid4(), uuid4())

    with pytest.raises(TokenInvalido):
        decodificar(token, TokenType.ACCESS)


def test_un_access_no_sirve_como_refresh() -> None:
    token, _ = emitir_access_token(uuid4(), uuid4())

    with pytest.raises(TokenInvalido):
        decodificar(token, TokenType.REFRESH)


def test_token_expirado_se_rechaza() -> None:
    ahora = datetime.now(UTC)
    payload = _payload_base(
        iat=ahora - timedelta(hours=2),
        nbf=ahora - timedelta(hours=2),
        exp=ahora - timedelta(hours=1),
    )
    token = jwt.encode(payload, get_settings().jwt_signing_key, algorithm=ALGORITMO)

    with pytest.raises(TokenInvalido):
        decodificar(token, TokenType.ACCESS)


def test_token_todavia_no_valido_se_rechaza() -> None:
    """`nbf` en el futuro: token pre-emitido que aún no debe aceptarse."""
    ahora = datetime.now(UTC)
    payload = _payload_base(
        nbf=ahora + timedelta(hours=1),
        exp=ahora + timedelta(hours=2),
    )
    token = jwt.encode(payload, get_settings().jwt_signing_key, algorithm=ALGORITMO)

    with pytest.raises(TokenInvalido):
        decodificar(token, TokenType.ACCESS)


def test_audiencia_incorrecta_se_rechaza() -> None:
    token = jwt.encode(
        _payload_base(aud="otra-api"),
        get_settings().jwt_signing_key,
        algorithm=ALGORITMO,
    )

    with pytest.raises(TokenInvalido):
        decodificar(token, TokenType.ACCESS)


def test_emisor_incorrecto_se_rechaza() -> None:
    token = jwt.encode(
        _payload_base(iss="otro-emisor"),
        get_settings().jwt_signing_key,
        algorithm=ALGORITMO,
    )

    with pytest.raises(TokenInvalido):
        decodificar(token, TokenType.ACCESS)


def test_firma_con_otra_clave_se_rechaza() -> None:
    token = jwt.encode(_payload_base(), "clave-de-un-atacante", algorithm=ALGORITMO)

    with pytest.raises(TokenInvalido):
        decodificar(token, TokenType.ACCESS)


def test_token_sin_algoritmo_se_rechaza() -> None:
    """`alg: none` es el ataque clásico contra implementaciones permisivas."""
    token = jwt.encode(_payload_base(), key="", algorithm="none")

    with pytest.raises(TokenInvalido):
        decodificar(token, TokenType.ACCESS)


@pytest.mark.parametrize("claim_faltante", ["iss", "aud", "sub", "sid", "jti", "typ", "exp", "nbf"])
def test_falta_un_claim_obligatorio(claim_faltante: str) -> None:
    payload = _payload_base()
    del payload[claim_faltante]
    token = jwt.encode(payload, get_settings().jwt_signing_key, algorithm=ALGORITMO)

    with pytest.raises(TokenInvalido):
        decodificar(token, TokenType.ACCESS)


def test_claim_con_contenido_no_parseable() -> None:
    """Firma válida no implica contenido válido: `sub` debe ser un UUID."""
    token = jwt.encode(
        _payload_base(sub="no-es-un-uuid"),
        get_settings().jwt_signing_key,
        algorithm=ALGORITMO,
    )

    with pytest.raises(TokenInvalido):
        decodificar(token, TokenType.ACCESS)


def test_token_basura_se_rechaza() -> None:
    with pytest.raises(TokenInvalido):
        decodificar("esto-no-es-un-jwt", TokenType.ACCESS)

"""Emisión y validación de access y refresh JWT.

Se valida cada claim, no solo la firma: `iss`, `aud`, `exp`, `nbf` y `typ`. Un
token con firma correcta pero emitido para otro entorno, o un refresh usado
donde se espera un access, se rechaza igual que uno falsificado.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid4

import jwt

from app.core.config import get_settings

ALGORITMO = "HS256"


class TokenType(StrEnum):
    ACCESS = "access"
    REFRESH = "refresh"


class TokenInvalido(Exception):
    """El token no es utilizable. No se detalla por qué: el motivo exacto es
    información útil para quien está probando tokens."""


@dataclass(frozen=True)
class ClaimsToken:
    user_id: UUID
    session_id: UUID
    jti: UUID
    tipo: TokenType
    expira_en: datetime


def _emitir(
    *,
    user_id: UUID,
    session_id: UUID,
    tipo: TokenType,
    ttl_segundos: int,
    jti: UUID | None = None,
) -> tuple[str, UUID, datetime]:
    settings = get_settings()
    ahora = datetime.now(UTC)
    expira = ahora + timedelta(seconds=ttl_segundos)
    identificador = jti or uuid4()

    payload = {
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "sub": str(user_id),
        "sid": str(session_id),
        "jti": str(identificador),
        "typ": tipo.value,
        "iat": ahora,
        "nbf": ahora,
        "exp": expira,
    }

    token = jwt.encode(payload, settings.jwt_signing_key, algorithm=ALGORITMO)
    return token, identificador, expira


def emitir_access_token(user_id: UUID, session_id: UUID) -> tuple[str, datetime]:
    token, _, expira = _emitir(
        user_id=user_id,
        session_id=session_id,
        tipo=TokenType.ACCESS,
        ttl_segundos=get_settings().access_token_ttl_seconds,
    )
    return token, expira


def emitir_refresh_token(
    user_id: UUID,
    session_id: UUID,
    jti: UUID | None = None,
) -> tuple[str, UUID, datetime]:
    """Devuelve (token, jti, expiración). El `jti` es la PK de `refresh_tokens`."""
    return _emitir(
        user_id=user_id,
        session_id=session_id,
        tipo=TokenType.REFRESH,
        ttl_segundos=get_settings().refresh_token_ttl_seconds,
        jti=jti,
    )


def decodificar(token: str, tipo_esperado: TokenType) -> ClaimsToken:
    """Valida firma y todos los claims. Lanza `TokenInvalido` si algo falla."""
    settings = get_settings()

    try:
        payload = jwt.decode(
            token,
            settings.jwt_signing_key,
            algorithms=[ALGORITMO],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            leeway=settings.jwt_leeway_seconds,
            options={
                "require": ["iss", "aud", "sub", "sid", "jti", "typ", "exp", "nbf"],
                "verify_signature": True,
                "verify_exp": True,
                "verify_nbf": True,
                "verify_iss": True,
                "verify_aud": True,
            },
        )
    except jwt.PyJWTError as error:
        raise TokenInvalido from error

    # `typ` no lo valida PyJWT: sin este chequeo, un refresh serviría como
    # access y saltaría la rotación entera.
    if payload.get("typ") != tipo_esperado.value:
        raise TokenInvalido

    try:
        return ClaimsToken(
            user_id=UUID(payload["sub"]),
            session_id=UUID(payload["sid"]),
            jti=UUID(payload["jti"]),
            tipo=tipo_esperado,
            expira_en=datetime.fromtimestamp(payload["exp"], tz=UTC),
        )
    except (ValueError, KeyError, TypeError) as error:
        # Claims presentes pero con contenido no parseable (un `sub` que no es
        # UUID). Firma válida no implica contenido válido.
        raise TokenInvalido from error

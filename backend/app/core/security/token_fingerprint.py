"""Fingerprint HMAC-SHA-256 para tokens de alta entropía.

Argon2id NO se usa aquí: está diseñado para contraseñas humanas, que tienen
poca entropía y necesitan ser costosas de probar. Un refresh token es un secreto
aleatorio de 256+ bits — no se puede adivinar por fuerza bruta, así que el costo
de Argon2 solo agregaría latencia a cada refresh sin ganar nada.

El HMAC se calcula con una clave dedicada (`refresh_fingerprint_key`), distinta
de la clave de firma JWT. Con eso, alguien que lea la base no puede reconstruir
el token ni calcular el fingerprint de uno propio para inyectarlo.
"""

import hashlib
import hmac

from app.core.config import get_settings


def fingerprint(token: str) -> bytes:
    """Huella determinista del token, para buscarlo en la base."""
    settings = get_settings()
    return hmac.new(
        settings.refresh_fingerprint_key.encode(),
        token.encode(),
        hashlib.sha256,
    ).digest()

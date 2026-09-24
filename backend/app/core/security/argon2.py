"""Hashing de contraseñas con Argon2id y compatibilidad legacy Django."""

import base64
import hashlib
import hmac
from dataclasses import dataclass

from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from argon2.low_level import Type
from pwdlib import PasswordHash
from pwdlib.exceptions import PwdlibError
from pwdlib.hashers.argon2 import Argon2Hasher

ARGON2_MEMORY_COST_KIB = 64 * 1024
ARGON2_TIME_COST = 3
ARGON2_PARALLELISM = 1
ARGON2_HASH_LEN = 32
ARGON2_SALT_LEN = 16

PREFIJO_ARGON2ID = "$argon2id$"
PREFIJO_DJANGO_PBKDF2_SHA256 = "pbkdf2_sha256$"


@dataclass(frozen=True)
class ResultadoVerificacion:
    """Resultado listo para el flujo de login del Paso 1.6."""

    valido: bool
    nuevo_hash: str | None = None


_argon2_hasher = Argon2Hasher(
    time_cost=ARGON2_TIME_COST,
    memory_cost=ARGON2_MEMORY_COST_KIB,
    parallelism=ARGON2_PARALLELISM,
    hash_len=ARGON2_HASH_LEN,
    salt_len=ARGON2_SALT_LEN,
    type=Type.ID,
)
_password_hash = PasswordHash((_argon2_hasher,))


def hash_password(plain: str) -> str:
    """Genera un hash Argon2id completo, con salt y parámetros codificados."""
    return _password_hash.hash(plain)


def verify_password(plain: str, hash: str) -> bool:  # noqa: A002
    """Verifica una contraseña contra Argon2id nuevo o PBKDF2-SHA256 legacy."""
    if hash.startswith(PREFIJO_ARGON2ID):
        try:
            return _password_hash.verify(plain, hash)
        except (InvalidHashError, VerificationError, VerifyMismatchError, PwdlibError):
            # PwdlibError cubre UnknownHashError: un hash Argon2 truncado o
            # corrupto en la base debe dar "credenciales inválidas", no un 500.
            # Es un caso real: el migrador legacy de Fase 5 copia hashes y una
            # fila dañada no puede tumbar el endpoint de login.
            return False

    if hash.startswith(PREFIJO_DJANGO_PBKDF2_SHA256):
        return _verificar_pbkdf2_django(plain, hash)

    return False


def needs_rehash(hash: str) -> bool:  # noqa: A002
    """Indica si el hash debe reemplazarse por el perfil Argon2id actual."""
    if hash.startswith(PREFIJO_DJANGO_PBKDF2_SHA256):
        return True
    if not hash.startswith(PREFIJO_ARGON2ID):
        return True
    try:
        return _argon2_hasher.check_needs_rehash(hash)
    except (InvalidHashError, VerificationError, PwdlibError):
        # Un hash que no se puede ni parsear se considera obsoleto: si el
        # usuario logra autenticarse por otra vía, hay que reemplazarlo.
        return True


def verificar_y_rehashear_si_corresponde(plain: str, hash_actual: str) -> ResultadoVerificacion:
    """Verifica y calcula el reemplazo Argon2id cuando el hash ya no cumple.

    El caller debe guardar `nuevo_hash` dentro de la misma transacción que el
    login exitoso. Esta función no toca la base para mantener el módulo puro.
    """
    if not verify_password(plain, hash_actual):
        return ResultadoVerificacion(valido=False)

    if needs_rehash(hash_actual):
        return ResultadoVerificacion(valido=True, nuevo_hash=hash_password(plain))

    return ResultadoVerificacion(valido=True)


def _verificar_pbkdf2_django(plain: str, encoded: str) -> bool:
    partes = encoded.split("$", 3)
    if len(partes) != 4:
        return False

    algoritmo, iteraciones_raw, salt, hash_base64 = partes
    if algoritmo != "pbkdf2_sha256":
        return False

    try:
        iteraciones = int(iteraciones_raw)
    except ValueError:
        return False

    if iteraciones <= 0:
        return False

    derived_key = hashlib.pbkdf2_hmac(
        "sha256",
        plain.encode(),
        salt.encode(),
        iteraciones,
    )
    calculado = base64.b64encode(derived_key).decode("ascii").strip()
    return hmac.compare_digest(calculado, hash_base64)

import time

import pytest
from argon2.low_level import Type
from pwdlib.hashers.argon2 import Argon2Hasher
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security.argon2 import (
    ARGON2_MEMORY_COST_KIB,
    ARGON2_PARALLELISM,
    ARGON2_TIME_COST,
    hash_password,
    needs_rehash,
    verificar_y_rehashear_si_corresponde,
    verify_password,
)

pytestmark = pytest.mark.security

PASSWORD_PRUEBA = "contrasena legacy de prueba 2026"
HASH_PBKDF2_DJANGO_PRUEBA = (
    "pbkdf2_sha256$720000$legacytestsalt2026$o2IbQuMq1RMZAl78zfC8CUtGy+TmtNPyP8wwZCKjmJs="
)


def test_hash_y_verificacion_round_trip() -> None:
    hash_generado = hash_password("una passphrase suficientemente larga")

    assert hash_generado.startswith("$argon2id$")
    assert verify_password("una passphrase suficientemente larga", hash_generado) is True
    assert verify_password("otra passphrase", hash_generado) is False


def test_verifica_hash_pbkdf2_django_de_prueba() -> None:
    assert verify_password(PASSWORD_PRUEBA, HASH_PBKDF2_DJANGO_PRUEBA) is True
    assert verify_password("password incorrecto", HASH_PBKDF2_DJANGO_PRUEBA) is False


def test_needs_rehash_detecta_legacy_y_argon2_actual() -> None:
    hash_actual = hash_password("otra passphrase suficientemente larga")

    assert needs_rehash(HASH_PBKDF2_DJANGO_PRUEBA) is True
    assert needs_rehash(hash_actual) is False


def test_needs_rehash_detecta_argon2_con_parametros_viejos() -> None:
    hasher_viejo = Argon2Hasher(
        time_cost=2,
        memory_cost=19 * 1024,
        parallelism=1,
        type=Type.ID,
    )
    hash_viejo = hasher_viejo.hash("otra passphrase suficientemente larga")

    assert needs_rehash(hash_viejo) is True


def test_parametros_cumplen_minimos_owasp() -> None:
    """La garantía de seguridad son los parámetros, no el reloj.

    OWASP publica como mínimo 19 MiB, 2 iteraciones y paralelismo 1 en su
    Password Storage Cheat Sheet. Estos valores son deterministas: valen igual
    en el portátil de desarrollo que en el servidor.
    """
    assert ARGON2_MEMORY_COST_KIB >= 19 * 1024
    assert ARGON2_TIME_COST >= 2
    assert ARGON2_PARALLELISM == 1


def test_el_hash_generado_lleva_los_parametros_configurados() -> None:
    """Que las constantes lleguen al hash, no solo al módulo."""
    hash_generado = hash_password("passphrase para verificar parámetros")

    parametros = hash_generado.split("$")[3]
    assert f"m={ARGON2_MEMORY_COST_KIB}" in parametros
    assert f"t={ARGON2_TIME_COST}" in parametros
    assert f"p={ARGON2_PARALLELISM}" in parametros


def test_el_costo_de_hashear_no_es_despreciable() -> None:
    """Detecta una configuración catastróficamente débil, no calibra el hardware.

    El umbral es holgado a propósito: con los parámetros actuales esto tarda
    ~85 ms en un portátil, pero un runner de CI más rápido no debe romper el
    pipeline. Lo que este test atrapa es un error de orden de magnitud — por
    ejemplo, bajar `memory_cost` a unos pocos KiB, que caería a menos de 5 ms.
    """
    duraciones = []
    for _ in range(3):
        inicio = time.perf_counter()
        hash_password("passphrase para medir costo de argon2id")
        duraciones.append((time.perf_counter() - inicio) * 1000)

    assert min(duraciones) >= 15


@pytest.mark.parametrize(
    "hash_invalido",
    [
        pytest.param("", id="vacio"),
        pytest.param("no-es-un-hash", id="basura"),
        pytest.param("$argon2id$v=19$m=65536", id="argon2-truncado"),
        pytest.param("pbkdf2_sha256$720000", id="pbkdf2-incompleto"),
        pytest.param("pbkdf2_sha256$abc$salt$aGFzaA==", id="iteraciones-no-numericas"),
        pytest.param("pbkdf2_sha256$0$salt$aGFzaA==", id="iteraciones-cero"),
        pytest.param("pbkdf2_sha256$-5$salt$aGFzaA==", id="iteraciones-negativas"),
        pytest.param("bcrypt_sha256$$2b$12$saltsaltsaltsaltsaltsu", id="bcrypt-django"),
        pytest.param("md5$salt$5f4dcc3b5aa765d61d8327deb882cf99", id="md5-django"),
        pytest.param("!contrasena-inutilizable-de-django", id="unusable-password-django"),
    ],
)
def test_hash_invalido_devuelve_false_sin_lanzar(hash_invalido: str) -> None:
    """Un hash corrupto da credenciales inválidas, nunca un 500.

    El migrador legacy de Fase 5 copia hashes desde la base vieja: una fila
    dañada no puede tumbar el endpoint de login. `!` es el prefijo que Django
    usa para contraseñas inutilizables, y también debe rechazarse.
    """
    assert verify_password("cualquier contrasena", hash_invalido) is False
    assert needs_rehash(hash_invalido) is True


def test_hash_pbkdf2_valido_con_password_incorrecto_no_rehashea() -> None:
    """Sin verificación exitosa no se calcula reemplazo: evita trabajo inútil
    y no revela nada por diferencia de tiempo entre usuarios."""
    resultado = verificar_y_rehashear_si_corresponde(
        "password incorrecto", HASH_PBKDF2_DJANGO_PRUEBA
    )

    assert resultado.valido is False
    assert resultado.nuevo_hash is None


def test_hash_argon2_actual_no_genera_rehash() -> None:
    hash_actual = hash_password("passphrase ya al dia")

    resultado = verificar_y_rehashear_si_corresponde("passphrase ya al dia", hash_actual)

    assert resultado.valido is True
    assert resultado.nuevo_hash is None


def test_dos_hashes_de_la_misma_password_son_distintos() -> None:
    """Salt aleatorio por hash: dos usuarios con la misma contraseña no se
    delatan entre sí en un volcado de la base."""
    passphrase = "la misma passphrase para dos usuarios"

    assert hash_password(passphrase) != hash_password(passphrase)


async def test_login_legacy_rehashea_en_la_misma_transaccion(session: AsyncSession) -> None:
    resultado = await session.execute(
        text("""
            INSERT INTO users (email, password_hash, first_name, last_name, status)
            VALUES (
                'legacy-password@amvarmar.test',
                :password_hash,
                'Legacy',
                'User',
                'ACTIVE'
            )
            RETURNING id
        """),
        {"password_hash": HASH_PBKDF2_DJANGO_PRUEBA},
    )
    user_id = resultado.scalar_one()

    fila = (
        await session.execute(
            text("SELECT password_hash FROM users WHERE id = :id FOR UPDATE"),
            {"id": user_id},
        )
    ).one()

    verificacion = verificar_y_rehashear_si_corresponde(PASSWORD_PRUEBA, fila.password_hash)
    assert verificacion.valido is True
    assert verificacion.nuevo_hash is not None

    await session.execute(
        text("UPDATE users SET password_hash = :hash WHERE id = :id"),
        {"hash": verificacion.nuevo_hash, "id": user_id},
    )

    hash_actualizado = (
        await session.execute(
            text("SELECT password_hash FROM users WHERE id = :id"),
            {"id": user_id},
        )
    ).scalar_one()

    assert hash_actualizado.startswith("$argon2id$")
    assert verify_password(PASSWORD_PRUEBA, hash_actualizado) is True
    assert needs_rehash(hash_actualizado) is False

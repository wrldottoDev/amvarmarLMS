"""Crea o promueve una cuenta de administrador.

Resuelve el problema del primer administrador: en una base recién migrada no
hay ningún `SUPER_ADMIN` —el migrador pone a todo el personal del legacy como
`OPS_ADMIN`— y sin uno nadie puede gestionar roles ni ajustes del sistema. Es
el único punto donde hace falta salirse de la interfaz.

    # Cuenta nueva, con contraseña generada
    python -m scripts.crear_admin --email yo@amvarmar.com --nombre Otoniel --apellido González

    # Promover una cuenta que ya existe (por ejemplo, una migrada del legacy)
    python -m scripts.crear_admin --email yo@amvarmar.com --promover

    # Con contraseña elegida, para no copiar y pegar
    python -m scripts.crear_admin --email yo@amvarmar.com --nombre Ana --apellido Mora \
        --password 'la-que-quieras-larga'

La contraseña se imprime UNA vez. No se guarda en claro y no hay forma de
volver a consultarla; si se pierde, se genera otra con `--restablecer`.
"""

import argparse
import asyncio
import secrets
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_engine, get_sessionmaker
from app.core.security.argon2 import hash_password
from app.modules.rbac.models import RoleCode, ScopeType

# Roles que este script puede asignar. Son los internos: un administrador no
# pertenece a ninguna empresa (ADR-0011), su alcance es global.
ROLES = {
    "SUPER_ADMIN": "Acceso completo, incluidos roles y ajustes del sistema.",
    "OPS_ADMIN": "Toda la operación, correcciones y auditoría. Sin gestión de roles.",
    "OPS_AGENT": "Operación diaria: cargas, estados y despachos.",
}


async def _existe(session: AsyncSession, email: str) -> UUID | None:
    fila: UUID | None = (
        await session.execute(
            text("SELECT id FROM users WHERE email = :e AND deleted_at IS NULL"),
            {"e": email},
        )
    ).scalar_one_or_none()
    return fila


async def _asignar_rol(session: AsyncSession, user_id: UUID, role_code: str) -> None:
    """Deja UN solo rol, con alcance global y sin empresa.

    Se borran los anteriores en vez de acumularlos: una cuenta con dos roles
    tendría la unión de sus permisos, y nadie mirando la tabla sabría cuál manda.
    """
    await session.execute(
        text("DELETE FROM user_role_assignments WHERE user_id = :u"), {"u": user_id}
    )
    asignado = (
        await session.execute(
            text("""
                INSERT INTO user_role_assignments (user_id, role_id, scope_type, company_id)
                SELECT :u, r.id, :alcance, NULL FROM roles r WHERE r.code = :rol
                RETURNING user_id
            """),
            {"u": user_id, "rol": role_code, "alcance": ScopeType.GLOBAL.value},
        )
    ).scalar_one_or_none()

    if asignado is None:
        raise SystemExit(
            f"El rol {role_code} no existe en la base. ¿Corriste `python -m scripts.seed_rbac`?"
        )

    # El personal interno no pertenece a ninguna empresa: si la cuenta venía de
    # una migración con membresía, se quita.
    await session.execute(
        text("DELETE FROM company_memberships WHERE user_id = :u"), {"u": user_id}
    )

    # Subir la versión invalida los permisos cacheados en Redis. Sin esto, el
    # rol nuevo tarda en aplicarse lo que dure la caché.
    await session.execute(
        text("UPDATE users SET authz_version = authz_version + 1 WHERE id = :u"), {"u": user_id}
    )


async def principal() -> None:
    parser = argparse.ArgumentParser(description="Crea o promueve una cuenta de administrador.")
    parser.add_argument("--email", required=True)
    parser.add_argument("--nombre", help="Obligatorio si la cuenta no existe.")
    parser.add_argument("--apellido", help="Obligatorio si la cuenta no existe.")
    parser.add_argument(
        "--rol",
        default=RoleCode.SUPER_ADMIN.value,
        choices=sorted(ROLES),
        help="Por defecto SUPER_ADMIN.",
    )
    parser.add_argument(
        "--password",
        help="Si no se indica, se genera una y se imprime.",
    )
    parser.add_argument(
        "--promover",
        action="store_true",
        help="La cuenta ya existe: solo cambiarle el rol, sin tocar su contraseña.",
    )
    parser.add_argument(
        "--restablecer",
        action="store_true",
        help="La cuenta ya existe: darle una contraseña nueva.",
    )
    argumentos = parser.parse_args()

    email = argumentos.email.strip().lower()

    async with get_sessionmaker()() as session:
        existente = await _existe(session, email)
        temporal: str | None = None

        if existente is None:
            if not argumentos.nombre or not argumentos.apellido:
                raise SystemExit(
                    f"No hay ninguna cuenta con {email}. "
                    "Para crearla hacen falta --nombre y --apellido."
                )

            temporal = argumentos.password or secrets.token_urlsafe(12)
            user_id = (
                await session.execute(
                    text("""
                        INSERT INTO users
                            (email, password_hash, first_name, last_name, status,
                             email_verified_at, must_change_password)
                        VALUES (:e, :h, :n, :a, 'ACTIVE', now(), :cambiar)
                        RETURNING id
                    """),
                    {
                        "e": email,
                        "h": hash_password(temporal),
                        "n": argumentos.nombre.strip()[:80],
                        "a": argumentos.apellido.strip()[:80],
                        # Si la eligió quien corre el script, ya la conoce y no
                        # tiene sentido obligar a cambiarla.
                        "cambiar": argumentos.password is None,
                    },
                )
            ).scalar_one()
            accion = "creada"
        else:
            user_id = existente
            accion = "promovida"

            if argumentos.restablecer or argumentos.password:
                temporal = argumentos.password or secrets.token_urlsafe(12)
                await session.execute(
                    text("""
                        UPDATE users
                        SET password_hash = :h, must_change_password = :cambiar,
                            password_changed_at = now(), failed_login_attempts = 0,
                            locked_until = NULL, status = 'ACTIVE', updated_at = now()
                        WHERE id = :u
                    """),
                    {
                        "h": hash_password(temporal),
                        "cambiar": argumentos.password is None,
                        "u": user_id,
                    },
                )
                # Cambiar la contraseña corta las sesiones abiertas: si se hace
                # porque se perdió el acceso, dejar viva una sesión ajena no
                # arregla nada.
                await session.execute(
                    text("""
                        UPDATE auth_sessions
                        SET revoked_at = now(), revoke_reason = 'PASSWORD_CHANGED'
                        WHERE user_id = :u AND revoked_at IS NULL
                    """),
                    {"u": user_id},
                )
            elif not argumentos.promover:
                raise SystemExit(
                    f"Ya existe una cuenta con {email}. "
                    "Usá --promover para cambiarle el rol, o --restablecer para darle "
                    "una contraseña nueva."
                )

        await _asignar_rol(session, user_id, argumentos.rol)
        await session.commit()

    await get_engine().dispose()

    print(f"\nCuenta {accion}: {email}")
    print(f"  rol: {argumentos.rol} — {ROLES[argumentos.rol]}")

    if temporal:
        print(f"  contraseña: {temporal}")
        print("\n  Se muestra una sola vez. No se guarda en claro en ningún lado.")
    else:
        print("  contraseña: sin cambios")

    print(
        "\n  Los permisos cacheados se invalidaron: el rol aplica en el próximo inicio de sesión."
    )


if __name__ == "__main__":
    asyncio.run(principal())

"""Administración de empresas y de sus usuarios.

Es lo que en el sistema viejo se hacía desde el admin de Django: dar de alta una
empresa cliente y crearle los usuarios que la van a manejar.

Reglas del modelo que este servicio hace cumplir:

- **Una persona pertenece a una sola empresa** (ADR-0011). No hay usuario que
  vea dos clientes.
- **El personal interno NO tiene empresa.** Su alcance es global; asignarle una
  lo convertiría en cliente de esa empresa.
- **Nada se borra de verdad.** Empresas y usuarios se desactivan con
  `deleted_at`, porque sus cargas, documentos y auditoría siguen existiendo y
  apuntando a ellos.
"""

import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflicto, RecursoNoEncontrado, ReglaDeNegocioViolada, SinPermiso
from app.core.security.argon2 import hash_password
from app.modules.rbac.catalog import Perm
from app.modules.rbac.models import RoleCode, ScopeType
from app.modules.rbac.service import PermisosEfectivos, invalidar_permisos

# Roles que un administrador puede asignar a un usuario de empresa cliente.
ROLES_DE_CLIENTE: frozenset[str] = frozenset({RoleCode.CLIENT_ADMIN, RoleCode.CLIENT_USER})

# Roles internos. Exigen `users.create.internal`, que es un permiso distinto.
ROLES_INTERNOS: frozenset[str] = frozenset(
    {RoleCode.SUPER_ADMIN, RoleCode.OPS_ADMIN, RoleCode.OPS_AGENT}
)


class DatosInvalidos(ReglaDeNegocioViolada):
    code = "DATOS_INVALIDOS"


class YaExiste(Conflicto):
    code = "YA_EXISTE"


@dataclass(frozen=True)
class EmpresaResumen:
    id: UUID
    legal_name: str
    trade_name: str | None
    tax_id: str | None
    status: str
    usuarios: int
    cargas: int
    created_at: datetime


@dataclass(frozen=True)
class UsuarioResumen:
    id: UUID
    email: str
    first_name: str
    last_name: str
    phone: str | None
    status: str
    role_code: str | None
    company_id: UUID | None
    company_name: str | None
    last_login_at: datetime | None
    created_at: datetime


def _exigir(permisos: PermisosEfectivos, permiso: str, company_id: UUID | None = None) -> None:
    if not permisos.permite(permiso, company_id=company_id):
        raise SinPermiso(f"Le falta el permiso {permiso}.")


# --- Empresas ---


async def listar_empresas(
    session: AsyncSession, *, permisos: PermisosEfectivos, incluir_inactivas: bool = False
) -> list[EmpresaResumen]:
    _exigir(permisos, Perm.COMPANIES_MANAGE)

    condicion = "" if incluir_inactivas else "WHERE c.deleted_at IS NULL"
    filas = (
        await session.execute(
            text(f"""
                SELECT c.id, c.legal_name, c.trade_name, c.tax_id, c.status, c.created_at,
                       (SELECT count(*) FROM company_memberships m
                         WHERE m.company_id = c.id AND m.status = 'ACTIVE') AS usuarios,
                       (SELECT count(*) FROM shipments s
                         WHERE s.company_id = c.id AND s.deleted_at IS NULL) AS cargas
                FROM companies c
                {condicion}
                ORDER BY c.legal_name
            """)  # noqa: S608
        )
    ).all()

    return [
        EmpresaResumen(
            id=f.id,
            legal_name=f.legal_name,
            trade_name=f.trade_name,
            tax_id=f.tax_id,
            status=f.status,
            usuarios=f.usuarios,
            cargas=f.cargas,
            created_at=f.created_at,
        )
        for f in filas
    ]


async def crear_empresa(
    session: AsyncSession,
    *,
    legal_name: str,
    trade_name: str | None,
    tax_id: str | None,
    actor_user_id: UUID,
    permisos: PermisosEfectivos,
) -> UUID:
    _exigir(permisos, Perm.COMPANIES_MANAGE)

    if not legal_name.strip():
        raise DatosInvalidos("El nombre legal es obligatorio.")

    try:
        empresa_id: UUID = (
            await session.execute(
                text("""
                    INSERT INTO companies (legal_name, trade_name, tax_id, status, created_by)
                    VALUES (:legal, :comercial, :cedula, 'ACTIVE', :actor)
                    RETURNING id
                """),
                {
                    "legal": legal_name.strip()[:255],
                    "comercial": (trade_name or "").strip()[:255] or None,
                    "cedula": (tax_id or "").strip()[:40] or None,
                    "actor": actor_user_id,
                },
            )
        ).scalar_one()
    except IntegrityError as error:
        # El índice único de cédula es parcial (solo empresas no borradas), así
        # que el choque real es contra otra empresa activa.
        raise YaExiste("Ya existe una empresa activa con esa cédula jurídica.") from error

    return empresa_id


async def actualizar_empresa(
    session: AsyncSession,
    *,
    company_id: UUID,
    cambios: dict[str, Any],
    permisos: PermisosEfectivos,
) -> None:
    _exigir(permisos, Perm.COMPANIES_MANAGE)

    campos = {
        "legal_name": "legal",
        "trade_name": "comercial",
        "tax_id": "cedula",
        "status": "estado",
    }
    aplicables = {c: v for c, v in cambios.items() if c in campos}
    if not aplicables:
        raise DatosInvalidos("No hay ningún campo editable en la solicitud.")

    asignaciones = ", ".join(f"{campo} = :{campos[campo]}" for campo in aplicables)
    parametros: dict[str, Any] = {campos[c]: v for c, v in aplicables.items()}
    parametros["id"] = company_id

    try:
        fila = (
            await session.execute(
                text(f"""
                    UPDATE companies SET {asignaciones}, updated_at = now()
                    WHERE id = :id AND deleted_at IS NULL
                    RETURNING id
                """),  # noqa: S608
                parametros,
            )
        ).scalar_one_or_none()
    except IntegrityError as error:
        raise YaExiste("Ya existe una empresa activa con esa cédula jurídica.") from error

    if fila is None:
        raise RecursoNoEncontrado("Empresa no encontrada.")


async def desactivar_empresa(
    session: AsyncSession, *, company_id: UUID, permisos: PermisosEfectivos
) -> int:
    """Desactiva la empresa y sus usuarios. Devuelve cuántos usuarios afectó.

    No borra: las cargas, documentos y auditoría de esa empresa siguen
    existiendo y apuntando a ella. Borrarla dejaría huérfano todo el historial.
    """
    _exigir(permisos, Perm.COMPANIES_MANAGE)

    empresa = (
        await session.execute(
            text("""
                UPDATE companies SET deleted_at = now(), status = 'CLOSED', updated_at = now()
                WHERE id = :id AND deleted_at IS NULL
                RETURNING id
            """),
            {"id": company_id},
        )
    ).scalar_one_or_none()

    if empresa is None:
        raise RecursoNoEncontrado("Empresa no encontrada.")

    usuarios = (
        await session.execute(
            text("""
                UPDATE users SET status = 'SUSPENDED',
                                 authz_version = authz_version + 1,
                                 updated_at = now()
                WHERE id IN (SELECT user_id FROM company_memberships WHERE company_id = :c)
                  AND deleted_at IS NULL
                RETURNING id
            """),
            {"c": company_id},
        )
    ).all()

    # Subir `authz_version` invalida los permisos cacheados: si no, quien
    # tuviera sesión abierta seguiría entrando hasta que expirara la caché.
    for fila in usuarios:
        await invalidar_permisos(session, fila.id)

    return len(usuarios)


# --- Usuarios ---


async def listar_usuarios(
    session: AsyncSession,
    *,
    permisos: PermisosEfectivos,
    company_id: UUID | None = None,
    incluir_inactivos: bool = False,
) -> list[UsuarioResumen]:
    """Usuarios que el actor puede administrar.

    Un `CLIENT_ADMIN` tiene `users.manage` con alcance de su empresa, así que ve
    solo a los suyos. El filtro va en el WHERE y no después.
    """
    if company_id is not None:
        _exigir(permisos, Perm.USERS_MANAGE, company_id)
    elif not any(p.code == Perm.USERS_MANAGE for p in permisos.permisos):
        # Sin empresa concreta no se puede exigir alcance sobre una: basta con
        # tener el permiso en algún alcance, y el WHERE de abajo acota el
        # resultado a las empresas que le corresponden.
        raise SinPermiso(f"Le falta el permiso {Perm.USERS_MANAGE}.")

    empresas_permitidas = [
        p.company_id
        for p in permisos.permisos
        if p.code == Perm.USERS_MANAGE and p.scope_type == ScopeType.ORGANIZATION and p.company_id
    ]
    es_global = any(
        p.code == Perm.USERS_MANAGE and p.scope_type == ScopeType.GLOBAL for p in permisos.permisos
    )

    condiciones = ["u.deleted_at IS NULL"] if not incluir_inactivos else ["true"]
    parametros: dict[str, Any] = {}

    if not es_global:
        condiciones.append("m.company_id = ANY(:permitidas)")
        parametros["permitidas"] = empresas_permitidas

    if company_id is not None:
        condiciones.append("m.company_id = :empresa")
        parametros["empresa"] = company_id

    filas = (
        await session.execute(
            text(f"""
                SELECT u.id, u.email, u.first_name, u.last_name, u.phone, u.status,
                       u.last_login_at, u.created_at,
                       r.code AS role_code, m.company_id, c.legal_name AS company_name
                FROM users u
                LEFT JOIN company_memberships m
                       ON m.user_id = u.id AND m.status = 'ACTIVE'
                LEFT JOIN companies c ON c.id = m.company_id
                LEFT JOIN user_role_assignments a ON a.user_id = u.id
                LEFT JOIN roles r ON r.id = a.role_id
                WHERE {" AND ".join(condiciones)}
                ORDER BY c.legal_name NULLS FIRST, u.first_name, u.last_name
            """),  # noqa: S608
            parametros,
        )
    ).all()

    return [
        UsuarioResumen(
            id=f.id,
            email=f.email,
            first_name=f.first_name,
            last_name=f.last_name,
            phone=f.phone,
            status=f.status,
            role_code=f.role_code,
            company_id=f.company_id,
            company_name=f.company_name,
            last_login_at=f.last_login_at,
            created_at=f.created_at,
        )
        for f in filas
    ]


@dataclass(frozen=True)
class UsuarioCreado:
    id: UUID
    email: str
    # Contraseña temporal. Se devuelve UNA sola vez, al crear: no se guarda en
    # claro en ningún lado y no hay forma de volver a consultarla.
    password_temporal: str


async def crear_usuario(
    session: AsyncSession,
    *,
    email: str,
    first_name: str,
    last_name: str,
    role_code: str,
    company_id: UUID | None,
    phone: str | None,
    permisos: PermisosEfectivos,
) -> UsuarioCreado:
    """Da de alta a una persona con su rol.

    Devuelve una contraseña temporal generada acá y marca la cuenta con
    `must_change_password`. Que la elija el administrador sería peor: quedaría
    escrita en un correo o un chat, y encima la conocería alguien más.
    """
    if role_code in ROLES_INTERNOS:
        _exigir(permisos, Perm.USERS_CREATE_INTERNAL)
        if company_id is not None:
            # ADR-0011: el personal interno no pertenece a una empresa.
            raise DatosInvalidos(
                "El personal interno no se asigna a una empresa: su alcance es global."
            )
    elif role_code in ROLES_DE_CLIENTE:
        if company_id is None:
            raise DatosInvalidos("Un usuario de cliente necesita una empresa.")
        _exigir(permisos, Perm.USERS_MANAGE, company_id)
    else:
        raise DatosInvalidos(f"El rol {role_code} no existe.")

    if company_id is not None and not await _empresa_activa(session, company_id):
        raise DatosInvalidos("La empresa indicada no existe o está inactiva.")

    # 12 bytes en base64 seguro: suficiente entropía y todavía dictable por
    # teléfono si hace falta.
    temporal = secrets.token_urlsafe(12)

    try:
        user_id: UUID = (
            await session.execute(
                text("""
                    INSERT INTO users
                        (email, password_hash, first_name, last_name, phone,
                         status, must_change_password)
                    VALUES (:email, :hash, :nombre, :apellido, :telefono, 'ACTIVE', true)
                    RETURNING id
                """),
                {
                    "email": email.strip().lower()[:255],
                    "hash": hash_password(temporal),
                    "nombre": first_name.strip()[:80],
                    "apellido": last_name.strip()[:80],
                    "telefono": (phone or "").strip()[:40] or None,
                },
            )
        ).scalar_one()
    except IntegrityError as error:
        raise YaExiste("Ya hay una cuenta con ese correo.") from error

    alcance = ScopeType.GLOBAL if company_id is None else ScopeType.ORGANIZATION
    await session.execute(
        text("""
            INSERT INTO user_role_assignments (user_id, role_id, scope_type, company_id)
            SELECT :u, r.id, :alcance, :empresa FROM roles r WHERE r.code = :rol
        """),
        {"u": user_id, "rol": role_code, "alcance": alcance.value, "empresa": company_id},
    )

    if company_id is not None:
        await session.execute(
            text("""
                INSERT INTO company_memberships (company_id, user_id, status)
                VALUES (:c, :u, 'ACTIVE')
            """),
            {"c": company_id, "u": user_id},
        )

    return UsuarioCreado(id=user_id, email=email.strip().lower(), password_temporal=temporal)


async def _empresa_activa(session: AsyncSession, company_id: UUID) -> bool:
    return bool(
        (
            await session.execute(
                text("""
                    SELECT 1 FROM companies
                    WHERE id = :c AND deleted_at IS NULL AND status = 'ACTIVE'
                """),
                {"c": company_id},
            )
        ).scalar_one_or_none()
    )


async def actualizar_usuario(
    session: AsyncSession,
    *,
    user_id: UUID,
    cambios: dict[str, Any],
    permisos: PermisosEfectivos,
) -> None:
    empresa = await _empresa_del_usuario(session, user_id)
    _exigir(permisos, Perm.USERS_MANAGE, empresa)

    campos = {
        "first_name": "nombre",
        "last_name": "apellido",
        "phone": "telefono",
        "status": "estado",
    }
    aplicables = {c: v for c, v in cambios.items() if c in campos}

    if aplicables:
        asignaciones = ", ".join(f"{campo} = :{campos[campo]}" for campo in aplicables)
        parametros: dict[str, Any] = {campos[c]: v for c, v in aplicables.items()}
        parametros["id"] = user_id
        fila = (
            await session.execute(
                text(f"""
                    UPDATE users SET {asignaciones}, updated_at = now()
                    WHERE id = :id AND deleted_at IS NULL
                    RETURNING id
                """),  # noqa: S608
                parametros,
            )
        ).scalar_one_or_none()
        if fila is None:
            raise RecursoNoEncontrado("Usuario no encontrado.")

    rol = cambios.get("role_code")
    if rol:
        await _cambiar_rol(session, user_id=user_id, role_code=rol, company_id=empresa)

    if aplicables.get("status") in {"SUSPENDED", "DISABLED"} or rol:
        # Cambiar rol o suspender tiene que surtir efecto ya, no cuando expire
        # la caché de permisos.
        await session.execute(
            text("UPDATE users SET authz_version = authz_version + 1 WHERE id = :id"),
            {"id": user_id},
        )
        await invalidar_permisos(session, user_id)


async def _cambiar_rol(
    session: AsyncSession, *, user_id: UUID, role_code: str, company_id: UUID | None
) -> None:
    if company_id is not None and role_code not in ROLES_DE_CLIENTE:
        raise DatosInvalidos("Un usuario de empresa solo puede tener un rol de cliente.")

    await session.execute(
        text("DELETE FROM user_role_assignments WHERE user_id = :u"), {"u": user_id}
    )
    alcance = ScopeType.GLOBAL if company_id is None else ScopeType.ORGANIZATION
    asignado = (
        await session.execute(
            text("""
                INSERT INTO user_role_assignments (user_id, role_id, scope_type, company_id)
                SELECT :u, r.id, :alcance, :empresa FROM roles r WHERE r.code = :rol
                RETURNING user_id
            """),
            {"u": user_id, "rol": role_code, "alcance": alcance.value, "empresa": company_id},
        )
    ).scalar_one_or_none()

    if asignado is None:
        raise DatosInvalidos(f"El rol {role_code} no existe.")


async def _empresa_del_usuario(session: AsyncSession, user_id: UUID) -> UUID | None:
    fila = (
        await session.execute(
            text("""
                SELECT m.company_id
                FROM users u
                LEFT JOIN company_memberships m
                       ON m.user_id = u.id AND m.status = 'ACTIVE'
                WHERE u.id = :u AND u.deleted_at IS NULL
            """),
            {"u": user_id},
        )
    ).one_or_none()

    if fila is None:
        raise RecursoNoEncontrado("Usuario no encontrado.")

    empresa: UUID | None = fila.company_id
    return empresa


async def restablecer_contrasena(
    session: AsyncSession, *, user_id: UUID, permisos: PermisosEfectivos
) -> str:
    """Genera una contraseña temporal nueva. La devuelve una sola vez."""
    empresa = await _empresa_del_usuario(session, user_id)
    _exigir(permisos, Perm.USERS_MANAGE, empresa)

    temporal = secrets.token_urlsafe(12)
    await session.execute(
        text("""
            UPDATE users
            SET password_hash = :hash, must_change_password = true,
                password_changed_at = now(), failed_login_attempts = 0,
                locked_until = NULL, authz_version = authz_version + 1,
                updated_at = now()
            WHERE id = :id
        """),
        {"hash": hash_password(temporal), "id": user_id},
    )

    # Todas las sesiones abiertas se cortan: si la contraseña se restablece por
    # sospecha de robo, dejar viva la sesión del atacante no arregla nada.
    await session.execute(
        text("""
            UPDATE auth_sessions SET revoked_at = now(), revoke_reason = 'PASSWORD_CHANGED'
            WHERE user_id = :id AND revoked_at IS NULL
        """),
        {"id": user_id},
    )
    await invalidar_permisos(session, user_id)

    return temporal


async def desactivar_usuario(
    session: AsyncSession, *, user_id: UUID, permisos: PermisosEfectivos
) -> None:
    empresa = await _empresa_del_usuario(session, user_id)
    _exigir(permisos, Perm.USERS_MANAGE, empresa)

    await session.execute(
        text("""
            UPDATE users
            SET status = 'SUSPENDED', deleted_at = now(),
                authz_version = authz_version + 1, updated_at = now()
            WHERE id = :id
        """),
        {"id": user_id},
    )
    await session.execute(
        text("""
            UPDATE auth_sessions SET revoked_at = now(), revoke_reason = 'ADMIN_REVOKED'
            WHERE user_id = :id AND revoked_at IS NULL
        """),
        {"id": user_id},
    )
    await invalidar_permisos(session, user_id)

"""Login, rotación de refresh, logout y detección de reutilización.

Todo el flujo de rotación ocurre dentro de una transacción. El caller hace el
commit: así el rehash progresivo de contraseña (Paso 1.5) y la emisión de
tokens se confirman juntos o no se confirman.
"""

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import ReglaDeNegocioViolada
from app.core.observability import metrics
from app.core.security.argon2 import hash_password, verificar_y_rehashear_si_corresponde
from app.core.security.jwt import (
    TokenInvalido,
    TokenType,
    decodificar,
    emitir_access_token,
    emitir_refresh_token,
)
from app.core.security.token_fingerprint import fingerprint
from app.modules.auth.models import ClientType, RevokeReason

# Hash señuelo: se verifica contra él cuando el email no existe, para que un
# login fallido tarde lo mismo exista o no la cuenta. Sin esto, la diferencia
# (~85 ms contra ~0 ms) permite enumerar qué correos están registrados.
_HASH_SENUELO = hash_password("señuelo-para-igualar-tiempos-de-respuesta")


class CredencialesInvalidas(Exception):
    """Credenciales incorrectas, cuenta bloqueada o inactiva.

    Una sola excepción para todos los casos: distinguirlos en la respuesta le
    diría a quien prueba credenciales si el correo existe o si la cuenta está
    bloqueada.
    """


class SesionInvalida(Exception):
    """El refresh no sirve: expirado, revocado, o reutilizado."""


class TokenRecuperacionInvalido(Exception):
    """El token de recuperación no existe, ya se usó o venció."""


class ReutilizacionDetectada(SesionInvalida):
    """Se presentó un refresh ya usado. La sesión completa queda revocada.

    Significa que el token salió del dispositivo legítimo: o lo robaron, o el
    atacante ya rotó y el usuario real llegó con el viejo. En ambos casos, la
    respuesta segura es cortar la sesión entera.
    """


@dataclass(frozen=True)
class TokensEmitidos:
    access_token: str
    access_expira_en: datetime
    refresh_token: str
    refresh_expira_en: datetime
    session_id: UUID


@dataclass(frozen=True)
class DatosCliente:
    client_type: ClientType = ClientType.WEB
    device_name: str | None = None
    ip: str | None = None
    user_agent: str | None = None


async def login(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    cliente: DatosCliente | None = None,
) -> TokensEmitidos:
    cliente = cliente or DatosCliente()

    fila = (
        await session.execute(
            text("""
                SELECT id, password_hash, status, locked_until
                FROM users
                WHERE email = :email AND deleted_at IS NULL
            """),
            {"email": email},
        )
    ).one_or_none()

    if fila is None:
        # Verificar contra el señuelo para gastar el mismo tiempo que un login
        # real antes de fallar.
        verificar_y_rehashear_si_corresponde(password, _HASH_SENUELO)
        metrics.login_fallido_total.labels(motivo="usuario_inexistente").inc()
        raise CredencialesInvalidas

    verificacion = verificar_y_rehashear_si_corresponde(password, fila.password_hash)

    if not verificacion.valido:
        await session.execute(
            text("""
                UPDATE users
                SET failed_login_attempts = failed_login_attempts + 1
                WHERE id = :user_id
            """),
            {"user_id": fila.id},
        )
        metrics.login_fallido_total.labels(motivo="password_incorrecta").inc()
        raise CredencialesInvalidas

    # El estado se revisa DESPUÉS de verificar la contraseña: comprobarlo antes
    # permitiría descubrir qué cuentas están suspendidas sin conocer la clave.
    ahora = datetime.now(UTC)
    if fila.status != "ACTIVE":
        metrics.login_fallido_total.labels(motivo="cuenta_inactiva").inc()
        raise CredencialesInvalidas
    if fila.locked_until is not None and fila.locked_until > ahora:
        metrics.login_fallido_total.labels(motivo="cuenta_bloqueada").inc()
        raise CredencialesInvalidas

    if verificacion.nuevo_hash is not None:
        # Rehash progresivo (Paso 1.5): en la misma transacción que el login.
        await session.execute(
            text("""
                UPDATE users
                SET password_hash = :hash, password_changed_at = now()
                WHERE id = :user_id
            """),
            {"hash": verificacion.nuevo_hash, "user_id": fila.id},
        )

    await session.execute(
        text("""
            UPDATE users
            SET failed_login_attempts = 0, last_login_at = now()
            WHERE id = :user_id
        """),
        {"user_id": fila.id},
    )

    return await _crear_sesion(session, user_id=fila.id, cliente=cliente)


async def _crear_sesion(
    session: AsyncSession,
    *,
    user_id: UUID,
    cliente: DatosCliente,
) -> TokensEmitidos:
    settings = get_settings()
    ahora = datetime.now(UTC)

    session_id = (
        await session.execute(
            text("""
                INSERT INTO auth_sessions (
                    user_id, client_type, device_name, ip_created, ip_last_used,
                    user_agent, idle_expires_at, absolute_expires_at
                )
                VALUES (
                    :user_id, :client_type, :device_name, :ip, :ip,
                    :user_agent, :idle_expires_at, :absolute_expires_at
                )
                RETURNING id
            """),
            {
                "user_id": user_id,
                "client_type": cliente.client_type.value,
                "device_name": cliente.device_name,
                "ip": cliente.ip,
                "user_agent": cliente.user_agent,
                "idle_expires_at": ahora + timedelta(seconds=settings.refresh_token_ttl_seconds),
                "absolute_expires_at": ahora
                + timedelta(seconds=settings.session_absolute_ttl_seconds),
            },
        )
    ).scalar_one()

    return await _emitir_par_de_tokens(session, user_id=user_id, session_id=session_id)


async def _emitir_par_de_tokens(
    session: AsyncSession,
    *,
    user_id: UUID,
    session_id: UUID,
    parent_token_id: UUID | None = None,
) -> TokensEmitidos:
    refresh_token, jti, refresh_expira = emitir_refresh_token(user_id, session_id)
    access_token, access_expira = emitir_access_token(user_id, session_id)

    await session.execute(
        text("""
            INSERT INTO refresh_tokens (
                id, session_id, token_hash, parent_token_id, expires_at
            )
            VALUES (:id, :session_id, :token_hash, :parent_token_id, :expires_at)
        """),
        {
            "id": jti,
            "session_id": session_id,
            "token_hash": fingerprint(refresh_token),
            "parent_token_id": parent_token_id,
            "expires_at": refresh_expira,
        },
    )

    if parent_token_id is not None:
        await session.execute(
            text("""
                UPDATE refresh_tokens SET replaced_by_token_id = :nuevo WHERE id = :viejo
            """),
            {"nuevo": jti, "viejo": parent_token_id},
        )

    return TokensEmitidos(
        access_token=access_token,
        access_expira_en=access_expira,
        refresh_token=refresh_token,
        refresh_expira_en=refresh_expira,
        session_id=session_id,
    )


async def rotar_refresh(session: AsyncSession, refresh_token: str) -> TokensEmitidos:
    """Consume un refresh y emite un par nuevo.

    Pasos, todos en la misma transacción:
    1. Validar JWT y claims completos.
    2. Buscar la fila por fingerprint.
    3. Si ya estaba usada, reuse detection: revocar la sesión entera.
    4. Marcar el token como usado (atómico, resuelve la carrera).
    5. Emitir el par nuevo.
    """
    try:
        claims = decodificar(refresh_token, TokenType.REFRESH)
    except TokenInvalido as error:
        raise SesionInvalida from error

    huella = fingerprint(refresh_token)

    fila = (
        await session.execute(
            text("""
                SELECT rt.id, rt.session_id, rt.used_at, rt.revoked_at, rt.expires_at,
                       s.user_id, s.revoked_at AS session_revoked_at,
                       s.idle_expires_at, s.absolute_expires_at
                FROM refresh_tokens rt
                JOIN auth_sessions s ON s.id = rt.session_id
                WHERE rt.token_hash = :token_hash
            """),
            {"token_hash": huella},
        )
    ).one_or_none()

    if fila is None or fila.id != claims.jti or fila.session_id != claims.session_id:
        # Firma válida pero sin fila: token de un entorno ya reseteado, o
        # fabricado con la clave filtrada. En ambos casos no se acepta.
        raise SesionInvalida

    if fila.used_at is not None:
        await _revocar_sesion(session, fila.session_id, RevokeReason.REUSE_DETECTED)
        metrics.refresh_reuse_total.inc()
        raise ReutilizacionDetectada

    ahora = datetime.now(UTC)
    if (
        fila.revoked_at is not None
        or fila.session_revoked_at is not None
        or fila.expires_at <= ahora
        or fila.idle_expires_at <= ahora
        or fila.absolute_expires_at <= ahora
    ):
        raise SesionInvalida

    # Marcado atómico: dos refresh simultáneos con el mismo token compiten aquí
    # y PostgreSQL serializa el UPDATE. El que llega segundo no encuentra fila
    # con `used_at IS NULL` y cae en reuse detection. Sin este WHERE, ambos
    # pasarían y se emitirían dos pares de tokens válidos.
    marcado = (
        await session.execute(
            text("""
                UPDATE refresh_tokens
                SET used_at = now()
                WHERE id = :id AND used_at IS NULL
                RETURNING id
            """),
            {"id": fila.id},
        )
    ).one_or_none()

    if marcado is None:
        await _revocar_sesion(session, fila.session_id, RevokeReason.REUSE_DETECTED)
        metrics.refresh_reuse_total.inc()
        raise ReutilizacionDetectada

    settings = get_settings()
    await session.execute(
        text("""
            UPDATE auth_sessions
            SET last_used_at = now(),
                idle_expires_at = now() + make_interval(secs => :idle_ttl)
            WHERE id = :session_id
        """),
        {"session_id": fila.session_id, "idle_ttl": settings.refresh_token_ttl_seconds},
    )

    return await _emitir_par_de_tokens(
        session,
        user_id=fila.user_id,
        session_id=fila.session_id,
        parent_token_id=fila.id,
    )


async def _revocar_sesion(
    session: AsyncSession,
    session_id: UUID,
    motivo: RevokeReason,
) -> None:
    await session.execute(
        text("""
            UPDATE auth_sessions
            SET revoked_at = now(), revoke_reason = :motivo
            WHERE id = :session_id AND revoked_at IS NULL
        """),
        {"session_id": session_id, "motivo": motivo.value},
    )
    await session.execute(
        text("""
            UPDATE refresh_tokens
            SET revoked_at = now()
            WHERE session_id = :session_id AND revoked_at IS NULL
        """),
        {"session_id": session_id},
    )


async def logout(session: AsyncSession, session_id: UUID) -> None:
    await _revocar_sesion(session, session_id, RevokeReason.LOGOUT)


async def logout_todas(session: AsyncSession, user_id: UUID) -> int:
    """Revoca todas las sesiones activas del usuario. Devuelve cuántas cerró."""
    return await _revocar_todas(session, user_id, RevokeReason.LOGOUT_ALL)


async def sesion_activa(session: AsyncSession, session_id: UUID) -> bool:
    """¿La sesión del access token sigue viva?

    Se consulta en cada request autenticado (Paso 1.8): un access token es
    válido criptográficamente hasta que expira, así que sin este chequeo un
    logout no tendría efecto durante los 10 minutos siguientes.
    """
    return (
        await session.execute(
            text("""
                SELECT 1 FROM auth_sessions
                WHERE id = :session_id
                  AND revoked_at IS NULL
                  AND idle_expires_at > now()
                  AND absolute_expires_at > now()
            """),
            {"session_id": session_id},
        )
    ).scalar_one_or_none() is not None


# --- Recuperación de contraseña (Paso 1.8) ---

# 1 hora: suficiente para que el usuario abra el correo, corto para que un
# enlace filtrado (reenvío, historial del navegador) deje de servir pronto.
TTL_PASSWORD_RESET_SEGUNDOS = 3600


async def crear_token_recuperacion(session: AsyncSession, email: str) -> tuple[str, UUID] | None:
    """Emite un token de recuperación. Devuelve None si el correo no existe.

    El caller responde siempre lo mismo exista o no la cuenta: el None solo
    indica que no hay correo que enviar, nunca llega al cliente.
    """
    user_id = (
        await session.execute(
            text("""
                SELECT id FROM users
                WHERE email = :email AND deleted_at IS NULL AND status <> 'DISABLED'
            """),
            {"email": email},
        )
    ).scalar_one_or_none()

    if user_id is None:
        return None

    # Invalidar los anteriores: pedir un enlace nuevo debe inutilizar el viejo,
    # o un correo antiguo reenviado seguiría sirviendo.
    await session.execute(
        text("""
            UPDATE one_time_tokens SET consumed_at = now()
            WHERE user_id = :user_id AND purpose = 'PASSWORD_RESET' AND consumed_at IS NULL
        """),
        {"user_id": user_id},
    )

    token = secrets.token_urlsafe(48)
    await session.execute(
        text("""
            INSERT INTO one_time_tokens (user_id, purpose, token_hash, expires_at)
            VALUES (:user_id, 'PASSWORD_RESET', :token_hash, :expires_at)
        """),
        {
            "user_id": user_id,
            "token_hash": fingerprint(token),
            "expires_at": datetime.now(UTC) + timedelta(seconds=TTL_PASSWORD_RESET_SEGUNDOS),
        },
    )

    return token, user_id


async def consumir_token_recuperacion(
    session: AsyncSession,
    *,
    token: str,
    nueva_password: str,
) -> UUID:
    """Consume el token y cambia la contraseña. Todo en una transacción.

    Revoca además todas las sesiones del usuario: si la contraseña se cambió
    porque alguien más la tenía, dejar sesiones vivas anularía el cambio.
    """
    return await _consumir_token_de_contrasena(
        session, token=token, proposito="PASSWORD_RESET", nueva_password=nueva_password
    )


async def _consumir_token_de_contrasena(
    session: AsyncSession,
    *,
    token: str,
    proposito: str,
    nueva_password: str,
) -> UUID:
    """Canje de un token de un solo uso por una contraseña nueva.

    Vale igual para la recuperación y para la invitación: en los dos casos
    alguien que probó su acceso al buzón elige una contraseña. El propósito va
    en el WHERE y no solo en la búsqueda, para que un token de invitación no
    sirva en el endpoint de recuperación ni al revés.
    """
    fila = (
        await session.execute(
            text("""
                UPDATE one_time_tokens
                SET consumed_at = now()
                WHERE token_hash = :token_hash
                  AND purpose = :proposito
                  AND consumed_at IS NULL
                  AND expires_at > now()
                RETURNING user_id
            """),
            {"token_hash": fingerprint(token), "proposito": proposito},
        )
    ).one_or_none()

    if fila is None:
        raise TokenRecuperacionInvalido

    await session.execute(
        text("""
            UPDATE users
            SET password_hash = :hash,
                password_changed_at = now(),
                must_change_password = false,
                failed_login_attempts = 0,
                locked_until = NULL
            WHERE id = :user_id
        """),
        {"hash": hash_password(nueva_password), "user_id": fila.user_id},
    )

    user_id: UUID = fila.user_id
    await _revocar_todas(session, user_id, RevokeReason.PASSWORD_CHANGED)

    return user_id


# --- Invitación (reemplaza el correo de credenciales del sistema anterior) ---

# 48 horas: el alta la hace un administrador en horario de oficina y la persona
# puede abrir el correo al día siguiente. Más largo que la recuperación porque
# nadie está esperando este enlace, más corto que "para siempre" porque sigue
# siendo un acceso a la cuenta.
TTL_INVITACION_SEGUNDOS = 48 * 3600


async def crear_token_invitacion(session: AsyncSession, user_id: UUID) -> str:
    """Emite el enlace de alta para una cuenta recién creada.

    A diferencia de la recuperación, no busca por correo: el llamador acaba de
    crear la fila y ya tiene el id. Buscar por correo acá abriría una vía para
    pedir una invitación de una cuenta ajena.
    """
    await session.execute(
        text("""
            UPDATE one_time_tokens SET consumed_at = now()
            WHERE user_id = :user_id AND purpose = 'INVITATION' AND consumed_at IS NULL
        """),
        {"user_id": user_id},
    )

    token = secrets.token_urlsafe(48)
    await session.execute(
        text("""
            INSERT INTO one_time_tokens (user_id, purpose, token_hash, expires_at)
            VALUES (:user_id, 'INVITATION', :token_hash, :expires_at)
        """),
        {
            "user_id": user_id,
            "token_hash": fingerprint(token),
            "expires_at": datetime.now(UTC) + timedelta(seconds=TTL_INVITACION_SEGUNDOS),
        },
    )

    return token


async def destinatario_de_invitacion(session: AsyncSession, token: str) -> Any | None:
    """Para quién es el enlace, sin consumirlo.

    La pantalla de alta muestra el nombre y el correo antes de pedir la
    contraseña, para que la persona sepa a qué cuenta está entrando. No
    consume el token: si consumiera, recargar la página lo invalidaría.
    """
    return (
        await session.execute(
            text("""
                SELECT u.email, u.first_name, u.last_name
                FROM one_time_tokens t
                JOIN users u ON u.id = t.user_id
                WHERE t.token_hash = :token_hash
                  AND t.purpose = 'INVITATION'
                  AND t.consumed_at IS NULL
                  AND t.expires_at > now()
                  AND u.deleted_at IS NULL
            """),
            {"token_hash": fingerprint(token)},
        )
    ).one_or_none()


async def consumir_token_invitacion(
    session: AsyncSession,
    *,
    token: str,
    nueva_password: str,
) -> UUID:
    """La persona elige su contraseña y la cuenta queda lista.

    Se revocan las sesiones igual que en la recuperación. Suena innecesario en
    una cuenta nueva, pero no lo es: el alta también entrega una contraseña
    temporal por si el correo no llega, y alguien pudo haber entrado con ella.
    """
    return await _consumir_token_de_contrasena(
        session, token=token, proposito="INVITATION", nueva_password=nueva_password
    )


async def _revocar_todas(session: AsyncSession, user_id: UUID, motivo: RevokeReason) -> int:
    sesiones = (
        (
            await session.execute(
                text("SELECT id FROM auth_sessions WHERE user_id = :u AND revoked_at IS NULL"),
                {"u": user_id},
            )
        )
        .scalars()
        .all()
    )
    for sid in sesiones:
        await _revocar_sesion(session, sid, motivo)
    return len(sesiones)


async def listar_sesiones(session: AsyncSession, user_id: UUID) -> list[Any]:
    return list(
        (
            await session.execute(
                text("""
                    SELECT id, client_type, device_name, host(ip_last_used) AS ip_last_used,
                           created_at, last_used_at
                    FROM auth_sessions
                    WHERE user_id = :user_id
                      AND revoked_at IS NULL
                      AND idle_expires_at > now()
                      AND absolute_expires_at > now()
                    ORDER BY last_used_at DESC
                """),
                {"user_id": user_id},
            )
        ).all()
    )


async def revocar_sesion_de(session: AsyncSession, *, user_id: UUID, session_id: UUID) -> bool:
    """Revoca una sesión propia. Devuelve False si no es del usuario.

    El filtro por `user_id` es lo que impide cerrar la sesión de otra persona
    conociendo su UUID.
    """
    pertenece = (
        await session.execute(
            text("SELECT 1 FROM auth_sessions WHERE id = :sid AND user_id = :uid"),
            {"sid": session_id, "uid": user_id},
        )
    ).scalar_one_or_none()

    if pertenece is None:
        return False

    await _revocar_sesion(session, session_id, RevokeReason.LOGOUT)
    return True


async def cambiar_contrasena_propia(
    session: AsyncSession,
    *,
    user_id: UUID,
    session_id: UUID,
    actual: str,
    nueva: str,
) -> None:
    """Cambia la contraseña de quien está autenticado.

    Exige la actual aunque ya haya sesión iniciada: si alguien deja el
    computador abierto, sin este paso puede cambiarle la contraseña a esa
    persona y quedarse con la cuenta.

    Al terminar se revocan las demás sesiones, pero NO la que hizo el cambio: si
    se cambió por sospecha de robo, hay que cortar la del atacante; cortar
    también la propia obligaría a entrar de nuevo sin ninguna razón.
    """
    fila = (
        await session.execute(
            text("""
                SELECT password_hash, must_change_password
                FROM users WHERE id = :u AND deleted_at IS NULL
            """),
            {"u": user_id},
        )
    ).one_or_none()

    if fila is None:
        raise CredencialesInvalidas

    verificacion = verificar_y_rehashear_si_corresponde(actual, fila.password_hash)
    if not verificacion.valido:
        metrics.login_fallido_total.labels(motivo="cambio_password_actual_incorrecta").inc()
        raise CredencialesInvalidas

    if actual == nueva:
        raise ReglaDeNegocioViolada(
            "La contraseña nueva tiene que ser distinta de la actual.",
            code="CONTRASENA_REPETIDA",
        )

    await session.execute(
        text("""
            UPDATE users
            SET password_hash = :hash,
                must_change_password = false,
                password_changed_at = now(),
                failed_login_attempts = 0,
                locked_until = NULL,
                updated_at = now()
            WHERE id = :u
        """),
        {"hash": hash_password(nueva), "u": user_id},
    )

    await session.execute(
        text("""
            UPDATE auth_sessions
            SET revoked_at = now(), revoke_reason = :motivo
            WHERE user_id = :u AND id <> :sesion AND revoked_at IS NULL
        """),
        {"motivo": RevokeReason.PASSWORD_CHANGED.value, "u": user_id, "sesion": session_id},
    )


async def actualizar_perfil_propio(
    session: AsyncSession,
    *,
    user_id: UUID,
    first_name: str | None,
    last_name: str | None,
    phone: str | None,
) -> None:
    """Datos que cada quien puede corregir de sí mismo.

    El correo NO está: es el identificador con el que entra y cambiarlo sin
    verificar el nuevo dejaría la cuenta sin forma de recuperarse. Eso lo hace
    un administrador.
    """
    cambios: dict[str, object] = {}
    if first_name is not None:
        cambios["first_name"] = first_name.strip()[:80]
    if last_name is not None:
        cambios["last_name"] = last_name.strip()[:80]
    if phone is not None:
        cambios["phone"] = phone.strip()[:40] or None

    if not cambios:
        return

    asignaciones = ", ".join(f"{campo} = :{campo}" for campo in cambios)
    await session.execute(
        text(f"UPDATE users SET {asignaciones}, updated_at = now() WHERE id = :u"),  # noqa: S608
        {**cambios, "u": user_id},
    )

"""Endpoints de autenticación (sección 5 del documento de arquitectura)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import rate_limit
from app.core.config import get_settings
from app.core.database import get_session
from app.core.errors import NoAutenticado, RecursoNoEncontrado, ReglaDeNegocioViolada
from app.core.logging import obtener_logger
from app.core.redis import get_redis
from app.modules.audit.models import Outcome
from app.modules.audit.service import registrar
from app.modules.auth import service
from app.modules.auth.dependencies import Actor, actor_actual
from app.modules.auth.schemas import (
    LoginRequest,
    MensajeResponse,
    MeResponse,
    PasswordForgotRequest,
    PasswordResetRequest,
    SessionResponse,
    TokenResponse,
)
from app.modules.auth.service import DatosCliente
from app.modules.notifications import service as notificaciones
from app.modules.rbac.service import obtener_permisos_efectivos

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

COOKIE_REFRESH = "amvarmar_refresh"
RUTA_REFRESH = "/api/v1/auth/refresh"

_log = obtener_logger("auth")

SesionDb = Annotated[AsyncSession, Depends(get_session)]
RedisDep = Annotated[Redis, Depends(get_redis)]
ActorDep = Annotated[Actor, Depends(actor_actual)]


def _poner_cookie_refresh(respuesta: Response, token: str, max_age: int) -> None:
    settings = get_settings()
    respuesta.set_cookie(
        key=COOKIE_REFRESH,
        value=token,
        max_age=max_age,
        # HttpOnly: inaccesible a JavaScript, que es la defensa contra XSS.
        httponly=True,
        # Secure salvo en local, donde se sirve por http y forzarlo impediría
        # probar el login.
        secure=settings.cookie_secure,
        # Strict: la cookie no viaja en peticiones originadas por otro sitio,
        # que es la defensa contra CSRF sobre el endpoint de refresh.
        samesite="strict",
        # Path acotado: el navegador solo la manda a /auth/refresh, así el
        # refresh nunca acompaña a una petición normal de la API.
        path=RUTA_REFRESH,
    )


def _borrar_cookie_refresh(respuesta: Response) -> None:
    respuesta.delete_cookie(key=COOKIE_REFRESH, path=RUTA_REFRESH)


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.post("/login", response_model=TokenResponse)
async def login(
    datos: LoginRequest,
    request: Request,
    respuesta: Response,
    db: SesionDb,
    redis: RedisDep,
) -> TokenResponse:
    ip = _ip(request)
    email_normalizado = datos.email.lower()

    # Dos límites: por cuenta frena el ataque dirigido; por IP, el masivo.
    await rate_limit.consumir(
        redis, clave=f"login:cuenta:{email_normalizado}", limite=rate_limit.LIMITE_LOGIN_POR_CUENTA
    )
    await rate_limit.consumir(redis, clave=f"login:ip:{ip}", limite=rate_limit.LIMITE_LOGIN_POR_IP)

    try:
        tokens = await service.login(
            db,
            email=datos.email,
            password=datos.password,
            cliente=DatosCliente(
                client_type=datos.client_type,
                device_name=datos.device_name,
                ip=ip,
                user_agent=request.headers.get("user-agent"),
            ),
        )
    except service.CredencialesInvalidas as error:
        await registrar(
            db,
            action="auth.login",
            resource_type="user",
            outcome=Outcome.FAILED,
            ip_address=ip,
            user_agent=request.headers.get("user-agent"),
            # Solo el correo intentado, nunca la contraseña.
            after_data={"email_intentado": datos.email},
        )
        await db.commit()
        raise NoAutenticado("Correo o contraseña incorrectos.") from error

    # Acertó la contraseña: no debe arrastrar los fallos previos.
    await rate_limit.limpiar(redis, clave=f"login:cuenta:{email_normalizado}")

    await registrar(
        db,
        action="auth.login",
        resource_type="auth_session",
        resource_id=tokens.session_id,
        outcome=Outcome.SUCCESS,
        ip_address=ip,
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()

    _poner_cookie_refresh(
        respuesta,
        tokens.refresh_token,
        max_age=get_settings().refresh_token_ttl_seconds,
    )
    return TokenResponse(access_token=tokens.access_token, expires_at=tokens.access_expira_en)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    request: Request,
    respuesta: Response,
    db: SesionDb,
    redis: RedisDep,
) -> TokenResponse:
    ip = _ip(request)
    await rate_limit.consumir(
        redis, clave=f"refresh:ip:{ip}", limite=rate_limit.LIMITE_REFRESH_POR_IP
    )

    token = request.cookies.get(COOKIE_REFRESH)
    if not token:
        raise NoAutenticado("Falta la cookie de refresco.")

    try:
        tokens = await service.rotar_refresh(db, token)
    except service.ReutilizacionDetectada as error:
        # Incidente de seguridad: la sesión ya quedó revocada dentro del
        # servicio y ese cambio SÍ se confirma.
        await registrar(
            db,
            action="auth.refresh.reuse_detected",
            resource_type="auth_session",
            outcome=Outcome.DENIED,
            ip_address=ip,
            user_agent=request.headers.get("user-agent"),
            reason="Se presentó un refresh token ya utilizado.",
        )
        await db.commit()
        _borrar_cookie_refresh(respuesta)
        _log.warning("refresh_reuse_detected", ip=ip)
        raise NoAutenticado("La sesión fue cerrada por seguridad.") from error
    except service.SesionInvalida as error:
        await db.rollback()
        _borrar_cookie_refresh(respuesta)
        raise NoAutenticado("Sesión inválida o expirada.") from error

    await db.commit()

    _poner_cookie_refresh(
        respuesta,
        tokens.refresh_token,
        max_age=get_settings().refresh_token_ttl_seconds,
    )
    return TokenResponse(access_token=tokens.access_token, expires_at=tokens.access_expira_en)


@router.post("/logout", response_model=MensajeResponse)
async def logout(
    request: Request,
    respuesta: Response,
    actor: ActorDep,
    db: SesionDb,
) -> MensajeResponse:
    await service.logout(db, actor.session_id)
    await registrar(
        db,
        action="auth.logout",
        resource_type="auth_session",
        resource_id=actor.session_id,
        actor_user_id=actor.user_id,
        ip_address=_ip(request),
    )
    await db.commit()

    _borrar_cookie_refresh(respuesta)
    return MensajeResponse(mensaje="Sesión cerrada.")


@router.post("/logout-all", response_model=MensajeResponse)
async def logout_all(
    request: Request,
    respuesta: Response,
    actor: ActorDep,
    db: SesionDb,
) -> MensajeResponse:
    cerradas = await service.logout_todas(db, actor.user_id)
    await registrar(
        db,
        action="auth.logout_all",
        resource_type="user",
        resource_id=actor.user_id,
        actor_user_id=actor.user_id,
        ip_address=_ip(request),
        after_data={"sesiones_cerradas": cerradas},
    )
    await db.commit()

    _borrar_cookie_refresh(respuesta)
    return MensajeResponse(mensaje=f"Se cerraron {cerradas} sesiones.")


@router.get("/sessions", response_model=list[SessionResponse])
async def listar_sesiones(actor: ActorDep, db: SesionDb) -> list[SessionResponse]:
    filas = await service.listar_sesiones(db, actor.user_id)
    return [
        SessionResponse(
            id=fila.id,
            client_type=fila.client_type,
            device_name=fila.device_name,
            ip_last_used=fila.ip_last_used,
            created_at=fila.created_at,
            last_used_at=fila.last_used_at,
            es_sesion_actual=fila.id == actor.session_id,
        )
        for fila in filas
    ]


@router.delete("/sessions/{session_id}", response_model=MensajeResponse)
async def revocar_sesion(
    session_id: str,
    request: Request,
    actor: ActorDep,
    db: SesionDb,
) -> MensajeResponse:
    from uuid import UUID

    try:
        objetivo = UUID(session_id)
    except ValueError as error:
        raise RecursoNoEncontrado("Sesión no encontrada.") from error

    if not await service.revocar_sesion_de(db, user_id=actor.user_id, session_id=objetivo):
        # 404 y no 403: confirmar que la sesión existe pero es de otra persona
        # ya es información sobre esa persona.
        raise RecursoNoEncontrado("Sesión no encontrada.")

    await registrar(
        db,
        action="auth.session.revoked",
        resource_type="auth_session",
        resource_id=objetivo,
        actor_user_id=actor.user_id,
        ip_address=_ip(request),
    )
    await db.commit()
    return MensajeResponse(mensaje="Sesión revocada.")


_MENSAJE_RECUPERACION = (
    "Si el correo corresponde a una cuenta, se enviaron las instrucciones "
    "para restablecer la contraseña."
)


@router.post("/password/forgot", response_model=MensajeResponse)
async def password_forgot(
    datos: PasswordForgotRequest,
    request: Request,
    db: SesionDb,
    redis: RedisDep,
) -> MensajeResponse:
    """Responde SIEMPRE lo mismo, exista o no la cuenta.

    Una respuesta distinta convertiría este endpoint en un verificador de qué
    correos están registrados.
    """
    await rate_limit.consumir(
        redis,
        clave=f"forgot:{datos.email.lower()}",
        limite=rate_limit.LIMITE_PASSWORD_FORGOT,
    )

    resultado = await service.crear_token_recuperacion(db, datos.email)

    if resultado is not None:
        token, user_id = resultado
        # Envío directo, no por el outbox: el outbox guardaría el token en claro
        # en la base y `one_time_tokens` guarda solo su huella justamente para
        # que eso no pase. Si el relay está caído se registra la entrega fallida
        # y la persona vuelve a pedirlo.
        enviado = await notificaciones.enviar_enlace_de_cuenta(
            db,
            user_id=user_id,
            event_code="account.password_reset",
            token=token,
        )
        await registrar(
            db,
            action="auth.password.forgot",
            resource_type="user",
            resource_id=user_id,
            outcome=Outcome.SUCCESS if enviado else Outcome.FAILED,
            ip_address=_ip(request),
            reason=None if enviado else "No se pudo entregar el correo de recuperación.",
        )
        await db.commit()

    # La respuesta es la misma haya salido el correo o no, e incluso si la
    # cuenta no existe: distinguir convertiría esto en un verificador de
    # correos registrados.
    return MensajeResponse(mensaje=_MENSAJE_RECUPERACION)


@router.post("/password/reset", response_model=MensajeResponse)
async def password_reset(
    datos: PasswordResetRequest,
    request: Request,
    respuesta: Response,
    db: SesionDb,
) -> MensajeResponse:
    try:
        user_id = await service.consumir_token_recuperacion(
            db, token=datos.token, nueva_password=datos.nueva_password
        )
    except service.TokenRecuperacionInvalido as error:
        await db.rollback()
        raise ReglaDeNegocioViolada(
            "El enlace de recuperación no es válido o ya venció.",
            code="TOKEN_RECUPERACION_INVALIDO",
        ) from error

    await registrar(
        db,
        action="auth.password.reset",
        resource_type="user",
        resource_id=user_id,
        actor_user_id=user_id,
        ip_address=_ip(request),
        reason="Contraseña restablecida; se revocaron todas las sesiones.",
    )
    await db.commit()

    _borrar_cookie_refresh(respuesta)
    return MensajeResponse(mensaje="Contraseña actualizada. Inicie sesión de nuevo.")


class InvitacionResponse(BaseModel):
    """A quién pertenece el enlace. Sin datos más allá de identificar la cuenta."""

    email: str
    first_name: str
    last_name: str


@router.get("/invitation/{token}", response_model=InvitacionResponse)
async def invitacion_detalle(token: str, db: SesionDb) -> InvitacionResponse:
    """Para quién es el enlace, sin consumirlo.

    La pantalla muestra el nombre y el correo antes de pedir la contraseña,
    para que la persona sepa a qué cuenta está entrando. No consume el token:
    si lo consumiera, recargar la página lo invalidaría.

    Acá sí se distingue entre válido e inválido, a diferencia de
    `/password/forgot`: el token es de 48 bytes aleatorios, así que responder
    no revela nada que no supiera ya quien lo tiene en la mano.
    """
    fila = await service.destinatario_de_invitacion(db, token)

    if fila is None:
        raise ReglaDeNegocioViolada(
            "El enlace de invitación no es válido o ya venció.",
            code="TOKEN_INVITACION_INVALIDO",
        )

    return InvitacionResponse(
        email=fila.email, first_name=fila.first_name, last_name=fila.last_name
    )


@router.post("/invitation/accept", response_model=MensajeResponse)
async def invitacion_aceptar(
    datos: PasswordResetRequest,
    request: Request,
    respuesta: Response,
    db: SesionDb,
) -> MensajeResponse:
    """La persona elige su contraseña y la cuenta queda lista para usar.

    Reusa `PasswordResetRequest` porque el cuerpo es idéntico —token más
    contraseña nueva— y la validación de fortaleza tiene que ser la misma. El
    propósito del token se comprueba en la consulta, así que uno de invitación
    no sirve en `/password/reset` ni al revés.
    """
    try:
        user_id = await service.consumir_token_invitacion(
            db, token=datos.token, nueva_password=datos.nueva_password
        )
    except service.TokenRecuperacionInvalido as error:
        await db.rollback()
        raise ReglaDeNegocioViolada(
            "El enlace de invitación no es válido o ya venció.",
            code="TOKEN_INVITACION_INVALIDO",
        ) from error

    await registrar(
        db,
        action="auth.invitation.accepted",
        resource_type="user",
        resource_id=user_id,
        actor_user_id=user_id,
        ip_address=_ip(request),
        reason="Alta completada: la persona eligió su contraseña.",
    )
    await db.commit()

    _borrar_cookie_refresh(respuesta)
    return MensajeResponse(mensaje="Contraseña creada. Ya puede iniciar sesión.")


me_router = APIRouter(prefix="/api/v1", tags=["auth"])


@me_router.get("/me", response_model=MeResponse, status_code=status.HTTP_200_OK)
async def me(actor: ActorDep, db: SesionDb, redis: RedisDep) -> MeResponse:
    """Usuario, empresa y permisos efectivos.

    El frontend usa `permisos` para ADAPTAR la interfaz, nunca para autorizar:
    el backend vuelve a verificar en cada request.
    """
    from sqlalchemy import text

    fila = (
        await db.execute(
            text("""
                SELECT u.id, u.email, u.first_name, u.last_name,
                       c.id AS company_id, c.legal_name, c.trade_name
                FROM users u
                LEFT JOIN company_memberships m
                       ON m.user_id = u.id AND m.status = 'ACTIVE'
                LEFT JOIN companies c ON c.id = m.company_id
                WHERE u.id = :user_id AND u.deleted_at IS NULL
            """),
            {"user_id": actor.user_id},
        )
    ).one_or_none()

    if fila is None:
        raise NoAutenticado("Usuario no disponible.")

    permisos = await obtener_permisos_efectivos(db, redis, actor.user_id)

    return MeResponse(
        id=fila.id,
        email=fila.email,
        first_name=fila.first_name,
        last_name=fila.last_name,
        empresa=None
        if fila.company_id is None
        else {
            "id": fila.company_id,
            "legal_name": fila.legal_name,
            "trade_name": fila.trade_name,
        },
        permisos=sorted(permisos.codigos()),
    )


class CambiarContrasenaRequest(BaseModel):
    # La actual se exige aunque ya haya sesión: si alguien deja el computador
    # abierto, sin este paso puede quedarse con la cuenta.
    actual: str = Field(min_length=1)
    nueva: str = Field(min_length=12, max_length=200)


class ActualizarPerfilRequest(BaseModel):
    """Lo que cada quien puede corregir de sí mismo.

    El correo NO está: es el identificador con el que entra, y cambiarlo sin
    verificar el nuevo dejaría la cuenta sin forma de recuperarse. Eso lo hace
    un administrador.
    """

    first_name: str | None = Field(default=None, min_length=1, max_length=80)
    last_name: str | None = Field(default=None, min_length=1, max_length=80)
    phone: str | None = Field(default=None, max_length=40)


@me_router.post("/me/password", response_model=MensajeResponse)
async def cambiar_mi_contrasena(
    datos: CambiarContrasenaRequest,
    request: Request,
    actor: ActorDep,
    db: SesionDb,
) -> MensajeResponse:
    """Cambia la contraseña propia y corta las demás sesiones.

    La sesión actual sobrevive: si se cambió por sospecha de robo hay que cortar
    la del atacante, y cortar también la propia obligaría a entrar de nuevo sin
    ninguna razón.
    """
    try:
        await service.cambiar_contrasena_propia(
            db,
            user_id=actor.user_id,
            session_id=actor.session_id,
            actual=datos.actual,
            nueva=datos.nueva,
        )
    except service.CredencialesInvalidas as error:
        await registrar(
            db,
            action="auth.password.change_denied",
            resource_type="user",
            resource_id=actor.user_id,
            actor_user_id=actor.user_id,
            outcome=Outcome.DENIED,
            reason="La contraseña actual no coincide",
            ip_address=_ip(request),
        )
        await db.commit()
        raise NoAutenticado("La contraseña actual no es correcta.") from error

    await registrar(
        db,
        action="auth.password.changed",
        resource_type="user",
        resource_id=actor.user_id,
        actor_user_id=actor.user_id,
        ip_address=_ip(request),
    )
    await db.commit()

    return MensajeResponse(mensaje="Contraseña actualizada. Las demás sesiones se cerraron.")


@me_router.patch("/me", response_model=MensajeResponse)
async def actualizar_mi_perfil(
    datos: ActualizarPerfilRequest,
    request: Request,
    actor: ActorDep,
    db: SesionDb,
) -> MensajeResponse:
    await service.actualizar_perfil_propio(
        db,
        user_id=actor.user_id,
        first_name=datos.first_name,
        last_name=datos.last_name,
        phone=datos.phone,
    )

    await registrar(
        db,
        action="user.profile.updated",
        resource_type="user",
        resource_id=actor.user_id,
        actor_user_id=actor.user_id,
        after_data={"campos": sorted(datos.model_dump(exclude_unset=True))},
        ip_address=_ip(request),
    )
    await db.commit()

    return MensajeResponse(mensaje="Datos actualizados.")

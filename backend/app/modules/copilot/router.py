"""Endpoints HTTP del asistente (ADR-0012).

`respond` transmite por SSE y NUNCA escribe datos de negocio directamente: el
ciclo de tool calling (`service.procesar_turno`) solo puede terminar en texto
o en una propuesta `PENDING`. La confirmación es un endpoint aparte,
autenticado igual que cualquier otro, con su propio permiso revalidado.
"""

import json
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, status
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_session
from app.core.errors import Conflicto, ErrorDeAplicacion, RecursoNoEncontrado
from app.core.idempotency import (
    buscar_respuesta_previa,
    guardar_respuesta,
    hash_de_solicitud,
    reservar,
)
from app.core.rate_limit import Limite, consumir, restantes
from app.core.redis import get_redis
from app.modules.audit.models import Outcome
from app.modules.audit.service import registrar
from app.modules.auth.dependencies import Actor, actor_actual
from app.modules.copilot import confirmaciones  # noqa: F401 -- registra REGISTRO_DE_CONFIRMACION
from app.modules.copilot.acciones import REGISTRO_DE_CONFIRMACION, AccionCopilot
from app.modules.copilot.models import EstadoPropuesta
from app.modules.copilot.provider import (
    ProveedorIA,
    ProveedorNoDisponible,
    ProveedorOpenAI,
    breaker_disponible,
)
from app.modules.copilot.provider_falso import ProveedorFalsoDeterministico
from app.modules.copilot.schemas import (
    CapabilitiesResponse,
    ConfirmarPropuestaRequest,
    PropuestaConfirmadaResponse,
    RespondRequest,
)
from app.modules.copilot.service import procesar_turno
from app.modules.copilot.tools import herramientas_disponibles
from app.modules.rbac.catalog import Perm
from app.modules.rbac.service import obtener_permisos_efectivos

router = APIRouter(prefix="/api/v1/copilot", tags=["asistente"])

SesionDb = Annotated[AsyncSession, Depends(get_session)]
RedisDep = Annotated[Redis, Depends(get_redis)]
ActorDep = Annotated[Actor, Depends(actor_actual)]


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


async def _contexto_actor(
    session: AsyncSession, user_id: UUID
) -> tuple[str, bool, str | None, UUID | None]:
    """Nombre, si es cliente, nombre de empresa, y `company_id` — para el
    system prompt y para el alcance de las propuestas que cree este turno.

    Mismo patrón de consulta que `GET /me`: sin ella el prompt no puede
    dirigirse a la persona por nombre ni saber de qué empresa es.
    """
    fila = (
        await session.execute(
            text("""
                SELECT u.first_name, u.last_name,
                       c.id AS company_id, c.legal_name, c.trade_name
                FROM users u
                LEFT JOIN company_memberships m
                       ON m.user_id = u.id AND m.status = 'ACTIVE'
                LEFT JOIN companies c ON c.id = m.company_id
                WHERE u.id = :user_id AND u.deleted_at IS NULL
            """),
            {"user_id": user_id},
        )
    ).one_or_none()

    if fila is None:
        raise RecursoNoEncontrado("Usuario no disponible.")

    nombre = f"{fila.first_name} {fila.last_name}".strip()
    es_cliente = fila.company_id is not None
    empresa_nombre = (fila.trade_name or fila.legal_name) if es_cliente else None
    return nombre, es_cliente, empresa_nombre, fila.company_id


def _limite_mensual_cliente() -> Limite:
    settings = get_settings()
    return Limite(
        intentos=settings.copilot_limite_mensajes_cliente_por_mes,
        ventana_segundos=30 * 24 * 3600,
    )


@router.get("/capabilities", response_model=CapabilitiesResponse)
async def capabilities(actor: ActorDep, db: SesionDb, redis: RedisDep) -> CapabilitiesResponse:
    """Nunca falla con 500: la caída del proveedor no puede parecer una caída
    del LMS. Si algo impide conversar, esto lo dice, no un error genérico."""
    settings = get_settings()
    permisos = await obtener_permisos_efectivos(db, redis, actor.user_id)

    disponible = (
        settings.copilot_habilitado
        and breaker_disponible()
        and Perm.COPILOT_USE in permisos.codigos()
    )

    _nombre, es_cliente, _empresa, _company_id = await _contexto_actor(db, actor.user_id)
    cuota_restante = (
        await restantes(
            redis, clave=f"copilot:mensual:{actor.user_id}", limite=_limite_mensual_cliente()
        )
        if es_cliente
        else None
    )

    herramientas = [h.nombre for h in herramientas_disponibles(permisos)]

    return CapabilitiesResponse(
        disponible=disponible,
        nombre=settings.copilot_name,
        cuota_restante=cuota_restante,
        herramientas=herramientas,
    )


def _formatear_sse(evento: str, datos: dict[str, Any]) -> str:
    return f"event: {evento}\ndata: {json.dumps(datos, ensure_ascii=False, default=str)}\n\n"


def _fabrica_proveedor() -> Callable[[], ProveedorIA]:
    """Dependencia de FastAPI para poder inyectar un proveedor falso en las
    pruebas (`app.dependency_overrides`) sin tocar la red. Devuelve una
    fábrica, no la instancia: instanciar `ProveedorOpenAI()` ya lanza si falta
    la clave, y ese chequeo lo hace el endpoint DESPUÉS de decidir si conviene
    la respuesta amigable por SSE en vez de un error crudo.

    `copilot_proveedor_falso` (solo `environment=local`, ver config.py) hace
    lo mismo pero para Playwright: un backend real, de punta a punta, sin
    tocar la API real de OpenAI."""
    if get_settings().copilot_proveedor_falso:
        return ProveedorFalsoDeterministico
    return ProveedorOpenAI


FabricaProveedorDep = Annotated[Callable[[], ProveedorIA], Depends(_fabrica_proveedor)]


@router.post("/respond")
async def respond(
    datos: RespondRequest,
    request: Request,
    actor: ActorDep,
    db: SesionDb,
    redis: RedisDep,
    fabrica_proveedor: FabricaProveedorDep,
) -> StreamingResponse:
    settings = get_settings()
    if not settings.copilot_habilitado:
        return StreamingResponse(
            iter(
                [
                    _formatear_sse(
                        "error",
                        {
                            "code": "COPILOT_NO_DISPONIBLE",
                            "message": "El asistente no está disponible.",
                        },
                    )
                ]
            ),
            media_type="text/event-stream",
        )

    permisos = await obtener_permisos_efectivos(db, redis, actor.user_id)
    if Perm.COPILOT_USE not in permisos.codigos():
        return StreamingResponse(
            iter(
                [
                    _formatear_sse(
                        "error", {"code": "SIN_PERMISO", "message": "No tenés acceso al asistente."}
                    )
                ]
            ),
            media_type="text/event-stream",
        )

    nombre_actor, es_cliente, empresa_nombre, company_id = await _contexto_actor(db, actor.user_id)

    if es_cliente:
        # ADR-0012: 100 mensajes por mes para clientes. `consumir` lanza
        # `DemasiadasSolicitudes` (429) si se pasó — el manejador global la
        # traduce, no hace falta capturarla acá.
        await consumir(
            redis, clave=f"copilot:mensual:{actor.user_id}", limite=_limite_mensual_cliente()
        )

    proveedor = fabrica_proveedor()

    async def flujo() -> AsyncIterator[str]:
        try:
            async for evento in procesar_turno(
                db,
                proveedor=proveedor,
                actor_user_id=actor.user_id,
                company_id=company_id,
                permisos=permisos,
                nombre_actor=nombre_actor,
                es_cliente=es_cliente,
                empresa_nombre=empresa_nombre,
                mensajes=[m.model_dump() for m in datos.mensajes],
            ):
                yield _formatear_sse(evento.evento, evento.datos)
        except ProveedorNoDisponible as error:
            yield _formatear_sse("error", {"code": "COPILOT_NO_DISPONIBLE", "message": str(error)})

    return StreamingResponse(
        flujo(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _propuesta_del_actor(
    session: AsyncSession, proposal_id: UUID, actor_user_id: UUID, *, bloquear: bool = False
) -> Any:
    """Ajena o inexistente responden igual: 404. Confirmar que existe pero es
    de otra persona ya sería información."""
    columnas = "id, created_by, company_id, action_code, payload, status, expires_at, result"
    if bloquear:
        consulta = text(
            f"SELECT {columnas} FROM copilot_action_proposals WHERE id = :id FOR UPDATE"  # noqa: S608
        )
    else:
        consulta = text(f"SELECT {columnas} FROM copilot_action_proposals WHERE id = :id")  # noqa: S608

    fila = (await session.execute(consulta, {"id": proposal_id})).one_or_none()

    if fila is None or fila.created_by != actor_user_id:
        raise RecursoNoEncontrado("Propuesta no encontrada.")

    return fila


@router.get("/proposals/{proposal_id}")
async def obtener_propuesta(proposal_id: UUID, actor: ActorDep, db: SesionDb) -> dict[str, Any]:
    propuesta = await _propuesta_del_actor(db, proposal_id, actor.user_id)
    return {
        "id": str(propuesta.id),
        "action_code": propuesta.action_code,
        "status": propuesta.status,
        "payload": propuesta.payload,
        "expires_at": propuesta.expires_at.isoformat(),
        "result": propuesta.result,
    }


@router.post("/proposals/{proposal_id}/confirm", response_model=PropuestaConfirmadaResponse)
async def confirmar_propuesta(
    proposal_id: UUID,
    datos: ConfirmarPropuestaRequest,
    request: Request,
    actor: ActorDep,
    db: SesionDb,
    redis: RedisDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> Any:
    """Confirma con el bloqueo de fila y las garantías del ADR: solo del
    dueño, solo PENDING y sin vencer, permiso revalidado AHORA, y ejecutada a
    través del registro cerrado de acciones — nunca de una ruta que el
    frontend haya elegido.
    """
    cuerpo = datos.model_dump(mode="json")
    huella = hash_de_solicitud(cuerpo)

    if idempotency_key:
        previa = await buscar_respuesta_previa(
            db, user_id=actor.user_id, key=idempotency_key, request_hash=huella
        )
        if previa is not None:
            return previa.body
        await reservar(db, user_id=actor.user_id, key=idempotency_key, request_hash=huella)

    propuesta = await _propuesta_del_actor(db, proposal_id, actor.user_id, bloquear=True)

    if propuesta.status != EstadoPropuesta.PENDING:
        raise Conflicto(
            f"La propuesta ya no está pendiente (estado: {propuesta.status}).",
            code="COPILOT_PROPUESTA_NO_PENDIENTE",
        )

    if propuesta.expires_at < datetime.now(UTC):
        await db.execute(
            text(
                "UPDATE copilot_action_proposals "
                "SET status = 'EXPIRED', resolved_at = now() WHERE id = :id"
            ),
            {"id": propuesta.id},
        )
        await db.commit()
        raise Conflicto(
            "La propuesta venció. Pedile a AMVI que la prepare de nuevo.",
            code="COPILOT_PROPUESTA_VENCIDA",
        )

    permisos = await obtener_permisos_efectivos(db, redis, actor.user_id)

    try:
        codigo = AccionCopilot(propuesta.action_code)
    except ValueError as error:
        raise Conflicto("Acción no reconocida.", code="COPILOT_ACCION_DESCONOCIDA") from error

    ejecutor = REGISTRO_DE_CONFIRMACION.get(codigo)
    if ejecutor is None:
        # Fundación (Fase 2): ningún ejecutor de confirmación está conectado
        # todavía. Ninguna propuesta real puede llegar hasta acá aún, pero el
        # mecanismo de bloqueo/verificación/idempotencia ya es el definitivo.
        raise Conflicto(
            "Esta acción todavía no se puede confirmar.", code="COPILOT_ACCION_NO_CONECTADA"
        )

    try:
        resultado = await ejecutor(db, permisos, propuesta.id, datos.campos)
    except ErrorDeAplicacion:
        # `gestion.crear` (u otro command reusado) puede rechazar el borrador
        # -- datos incompletos, permiso revocado desde que se propuso, etc.
        # Sin este `except`, la excepción cierra la sesión sin commitear y la
        # fila queda `PENDING` para siempre: `FAILED` (ADR-0012) es un estado
        # alcanzable, no un valor del enum que nadie escribe nunca.
        #
        # El `rollback` es obligatorio, no cosmético: si el ejecutor llegó a
        # emitir SQL antes de fallar (p. ej. `gestion.crear` a mitad de una
        # validación), Postgres deja la transacción abortada — cualquier
        # sentencia posterior en la misma transacción, incluida esta misma
        # actualización a `FAILED`, moriría con "current transaction is
        # aborted" si no se limpia primero.
        await db.rollback()
        await db.execute(
            text(
                "UPDATE copilot_action_proposals "
                "SET status = 'FAILED', resolved_at = now() WHERE id = :id"
            ),
            {"id": propuesta.id},
        )
        await registrar(
            db,
            action="copilot.proposal.confirmed",
            resource_type="copilot_action_proposal",
            resource_id=propuesta.id,
            outcome=Outcome.FAILED,
            actor_user_id=actor.user_id,
            company_id=propuesta.company_id,
            after_data={"action_code": propuesta.action_code},
            ip_address=_ip(request),
        )
        await db.commit()
        raise

    await db.execute(
        text(
            "UPDATE copilot_action_proposals "
            "SET status = 'CONFIRMED', resolved_at = now(), result = :result WHERE id = :id"
        ),
        {"id": propuesta.id, "result": json.dumps(resultado, default=str)},
    )

    await registrar(
        db,
        action="copilot.proposal.confirmed",
        resource_type="copilot_action_proposal",
        resource_id=propuesta.id,
        actor_user_id=actor.user_id,
        company_id=propuesta.company_id,
        after_data={"action_code": propuesta.action_code},
        ip_address=_ip(request),
    )

    cuerpo_respuesta = PropuestaConfirmadaResponse(
        id=str(propuesta.id), action_code=propuesta.action_code, resultado=resultado
    ).model_dump(mode="json")

    if idempotency_key:
        await guardar_respuesta(
            db,
            user_id=actor.user_id,
            key=idempotency_key,
            status_code=status.HTTP_200_OK,
            body=cuerpo_respuesta,
        )

    await db.commit()
    return cuerpo_respuesta


@router.post("/proposals/{proposal_id}/reject")
async def rechazar_propuesta(
    proposal_id: UUID, request: Request, actor: ActorDep, db: SesionDb
) -> dict[str, str]:
    propuesta = await _propuesta_del_actor(db, proposal_id, actor.user_id, bloquear=True)

    if propuesta.status != EstadoPropuesta.PENDING:
        raise Conflicto(
            f"La propuesta ya no está pendiente (estado: {propuesta.status}).",
            code="COPILOT_PROPUESTA_NO_PENDIENTE",
        )

    await db.execute(
        text(
            "UPDATE copilot_action_proposals "
            "SET status = 'REJECTED', resolved_at = now() WHERE id = :id"
        ),
        {"id": propuesta.id},
    )

    await registrar(
        db,
        action="copilot.proposal.rejected",
        resource_type="copilot_action_proposal",
        resource_id=propuesta.id,
        actor_user_id=actor.user_id,
        company_id=propuesta.company_id,
        outcome=Outcome.SUCCESS,
        ip_address=_ip(request),
    )

    await db.commit()
    return {"status": "REJECTED"}

"""Endpoints de solicitudes de despacho (Paso 3.3)."""

from datetime import date, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, status
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.errors import ErrorDeAplicacion, RecursoNoEncontrado
from app.core.idempotency import (
    buscar_respuesta_previa,
    guardar_respuesta,
    hash_de_solicitud,
    reservar,
)
from app.core.pagination import Cursor, Pagina, armar_pagina, normalizar_limite
from app.core.redis import get_redis
from app.modules.audit.models import Outcome
from app.modules.audit.service import registrar
from app.modules.auth.dependencies import Actor, actor_actual
from app.modules.dispatches import service
from app.modules.dispatches.models import DispatchMethod, DispatchStatus
from app.modules.rbac.catalog import Perm
from app.modules.rbac.service import PermisosEfectivos, obtener_permisos_efectivos
from app.modules.shipments.queries import alcance_de_lectura

router = APIRouter(prefix="/api/v1/dispatch-requests", tags=["dispatches"])

SesionDb = Annotated[AsyncSession, Depends(get_session)]
RedisDep = Annotated[Redis, Depends(get_redis)]
ActorDep = Annotated[Actor, Depends(actor_actual)]


class CrearSolicitudRequest(BaseModel):
    method: DispatchMethod
    shipment_ids: list[UUID] = Field(min_length=1, max_length=100)
    delivery_address: str | None = Field(default=None, max_length=2000)
    instructions: str | None = Field(default=None, max_length=2000)
    requested_pickup_date: date | None = None


class AccionRequest(BaseModel):
    notes: str | None = Field(default=None, max_length=2000)
    row_version: int | None = Field(default=None, ge=1)


class RechazoRequest(BaseModel):
    # Obligatorio: quien recibe el rechazo necesita saber qué corregir.
    reason: str = Field(min_length=1, max_length=2000)
    row_version: int | None = Field(default=None, ge=1)


class SolicitudResponse(BaseModel):
    id: UUID
    dispatch_number: str
    status: str
    method: str
    company_id: UUID
    shipment_count: int
    requested_at: datetime
    row_version: int


class DetalleSolicitudResponse(SolicitudResponse):
    delivery_address: str | None
    instructions: str | None
    requested_pickup_date: date | None
    rejected_reason: str | None
    approved_at: datetime | None
    completed_at: datetime | None
    shipment_ids: list[UUID]


class AccionResponse(BaseModel):
    id: UUID
    from_status: str
    to_status: str
    row_version: int


class PaginaSolicitudes(BaseModel):
    items: list[SolicitudResponse]
    next_cursor: str | None
    has_more: bool


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _empresas_del_actor(permisos: PermisosEfectivos) -> list[UUID] | None:
    """`None` = alcance global (Operaciones). Lista = las empresas del cliente."""
    alcance = alcance_de_lectura(permisos)
    if alcance.global_:
        return None
    return alcance.company_ids


def _exigir(permisos: PermisosEfectivos, codigo: str, company_id: UUID | None) -> None:
    """404 y no 403: confirmar que existe pero no es tuyo ya es información."""
    if company_id is None:
        # Sin empresa objetivo, solo alcance global sirve.
        if not permisos.permite(codigo):
            raise RecursoNoEncontrado("Solicitud de despacho no encontrada.")
        return
    if not permisos.permite(codigo, company_id=company_id):
        raise RecursoNoEncontrado("Solicitud de despacho no encontrada.")


async def _empresa_del_actor_o_404(
    db: AsyncSession, permisos: PermisosEfectivos, company_id: UUID | None
) -> UUID:
    """Empresa sobre la que se crea la solicitud.

    Un cliente crea en la suya. Operaciones debe indicar cuál, porque tiene
    alcance sobre todas y adivinar sería arbitrario.
    """
    empresas = _empresas_del_actor(permisos)

    if empresas is None:
        if company_id is None:
            raise RecursoNoEncontrado("Indicá la empresa para la que se crea la solicitud.")
        return company_id

    if not empresas:
        raise RecursoNoEncontrado("Solicitud de despacho no encontrada.")

    if company_id is not None and company_id not in empresas:
        raise RecursoNoEncontrado("Solicitud de despacho no encontrada.")

    return empresas[0]


@router.post("", response_model=DetalleSolicitudResponse, status_code=status.HTTP_201_CREATED)
async def crear_solicitud(
    datos: CrearSolicitudRequest,
    request: Request,
    actor: ActorDep,
    db: SesionDb,
    redis: RedisDep,
    company_id: UUID | None = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> Any:
    """Crea la solicitud y reclama las cargas.

    Acepta `Idempotency-Key`: reintentar tras un timeout de red no debe crear
    dos solicitudes ni bloquear las cargas dos veces.
    """
    permisos = await obtener_permisos_efectivos(db, redis, actor.user_id)
    empresa = await _empresa_del_actor_o_404(db, permisos, company_id)
    _exigir(permisos, Perm.DISPATCH_REQUESTS_CREATE, empresa)

    cuerpo = datos.model_dump(mode="json")
    huella = hash_de_solicitud(cuerpo)

    if idempotency_key:
        previa = await buscar_respuesta_previa(
            db, user_id=actor.user_id, key=idempotency_key, request_hash=huella
        )
        if previa is not None:
            return previa.body
        await reservar(db, user_id=actor.user_id, key=idempotency_key, request_hash=huella)

    solicitud = await service.crear(
        db,
        company_id=empresa,
        actor_user_id=actor.user_id,
        method=datos.method.value,
        shipment_ids=datos.shipment_ids,
        permisos=permisos,
        delivery_address=datos.delivery_address,
        instructions=datos.instructions,
        requested_pickup_date=datos.requested_pickup_date,
    )

    await registrar(
        db,
        action="dispatch.created",
        resource_type="dispatch_request",
        resource_id=solicitud.id,
        actor_user_id=actor.user_id,
        company_id=empresa,
        after_data={
            "dispatch_number": solicitud.dispatch_number,
            "cargas": len(solicitud.shipment_ids),
            "method": datos.method.value,
        },
        ip_address=_ip(request),
    )

    detalle = await _detalle(db, solicitud.id, _empresas_del_actor(permisos))
    cuerpo_respuesta = detalle.model_dump(mode="json")

    if idempotency_key:
        await guardar_respuesta(
            db,
            user_id=actor.user_id,
            key=idempotency_key,
            status_code=status.HTTP_201_CREATED,
            body=cuerpo_respuesta,
        )

    await db.commit()
    return cuerpo_respuesta


async def _detalle(
    db: AsyncSession, dispatch_id: UUID, empresas: list[UUID] | None
) -> DetalleSolicitudResponse:
    condiciones = ["d.id = :id"]
    parametros: dict[str, Any] = {"id": dispatch_id}
    if empresas is not None:
        condiciones.append("d.company_id = ANY(:empresas)")
        parametros["empresas"] = empresas

    consulta = f"""
        SELECT d.id, d.dispatch_number, d.status, d.method, d.company_id,
               d.delivery_address, d.instructions, d.requested_pickup_date,
               d.rejected_reason, d.requested_at, d.approved_at, d.completed_at,
               d.row_version,
               COALESCE(
                   array_agg(s.shipment_id ORDER BY s.shipment_id)
                   FILTER (WHERE s.shipment_id IS NOT NULL AND s.released_at IS NULL),
                   '{{}}'
               ) AS shipment_ids
        FROM dispatch_requests d
        LEFT JOIN dispatch_request_shipments s ON s.dispatch_request_id = d.id
        WHERE {" AND ".join(condiciones)}
        GROUP BY d.id
    """  # noqa: S608

    fila = (await db.execute(text(consulta), parametros)).one_or_none()
    if fila is None:
        raise RecursoNoEncontrado("Solicitud de despacho no encontrada.")

    return DetalleSolicitudResponse(
        id=fila.id,
        dispatch_number=fila.dispatch_number,
        status=fila.status,
        method=fila.method,
        company_id=fila.company_id,
        shipment_count=len(fila.shipment_ids),
        requested_at=fila.requested_at,
        row_version=fila.row_version,
        delivery_address=fila.delivery_address,
        instructions=fila.instructions,
        requested_pickup_date=fila.requested_pickup_date,
        rejected_reason=fila.rejected_reason,
        approved_at=fila.approved_at,
        completed_at=fila.completed_at,
        shipment_ids=list(fila.shipment_ids),
    )


@router.get("", response_model=PaginaSolicitudes)
async def listar(
    actor: ActorDep,
    db: SesionDb,
    redis: RedisDep,
    limit: Annotated[int | None, Query(ge=1)] = None,
    cursor: str | None = None,
    status_filtro: Annotated[list[DispatchStatus] | None, Query(alias="status")] = None,
) -> PaginaSolicitudes:
    permisos = await obtener_permisos_efectivos(db, redis, actor.user_id)
    empresas = _empresas_del_actor(permisos)

    if empresas is not None and not empresas:
        return PaginaSolicitudes(items=[], next_cursor=None, has_more=False)

    limite = normalizar_limite(limit)
    condiciones: list[str] = []
    parametros: dict[str, Any] = {"limite": limite + 1}

    if empresas is not None:
        condiciones.append("d.company_id = ANY(:empresas)")
        parametros["empresas"] = empresas

    if status_filtro:
        condiciones.append("d.status = ANY(:estados)")
        parametros["estados"] = [e.value for e in status_filtro]

    if cursor:
        posicion = Cursor.decodificar(cursor)
        condiciones.append("(d.requested_at, d.id) < (:cursor_fecha, :cursor_id)")
        parametros["cursor_fecha"] = posicion.created_at
        parametros["cursor_id"] = posicion.id

    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    consulta = f"""
        SELECT d.id, d.dispatch_number, d.status, d.method, d.company_id,
               d.requested_at, d.row_version,
               count(s.shipment_id) FILTER (WHERE s.released_at IS NULL) AS cargas
        FROM dispatch_requests d
        LEFT JOIN dispatch_request_shipments s ON s.dispatch_request_id = d.id
        {where}
        GROUP BY d.id
        ORDER BY d.requested_at DESC, d.id DESC
        LIMIT :limite
    """  # noqa: S608

    filas = list((await db.execute(text(consulta), parametros)).all())
    pagina: Pagina[Any] = armar_pagina(
        filas,
        limite=limite,
        cursor_de=lambda f: Cursor(created_at=f.requested_at, id=f.id),
    )

    return PaginaSolicitudes(
        items=[
            SolicitudResponse(
                id=f.id,
                dispatch_number=f.dispatch_number,
                status=f.status,
                method=f.method,
                company_id=f.company_id,
                shipment_count=f.cargas,
                requested_at=f.requested_at,
                row_version=f.row_version,
            )
            for f in pagina.items
        ],
        next_cursor=pagina.next_cursor,
        has_more=pagina.has_more,
    )


@router.get("/{dispatch_id}", response_model=DetalleSolicitudResponse)
async def obtener(
    dispatch_id: UUID, actor: ActorDep, db: SesionDb, redis: RedisDep
) -> DetalleSolicitudResponse:
    permisos = await obtener_permisos_efectivos(db, redis, actor.user_id)
    return await _detalle(db, dispatch_id, _empresas_del_actor(permisos))


async def _ejecutar_accion(
    db: AsyncSession,
    request: Request,
    actor: Actor,
    permisos: PermisosEfectivos,
    dispatch_id: UUID,
    *,
    permiso: str,
    accion: str,
    ejecutar: Any,
) -> AccionResponse:
    """Verifica permiso sobre la empresa dueña, ejecuta y audita.

    Un intento denegado también deja rastro: una racha de ellos es el patrón de
    alguien tanteando permisos.
    """
    empresas = _empresas_del_actor(permisos)
    empresa = (
        await db.execute(
            text("SELECT company_id FROM dispatch_requests WHERE id = :id"),
            {"id": dispatch_id},
        )
    ).scalar_one_or_none()

    if empresa is None or (empresas is not None and empresa not in empresas):
        raise RecursoNoEncontrado("Solicitud de despacho no encontrada.")

    _exigir(permisos, permiso, empresa)

    try:
        resultado = await ejecutar(empresas)
    except ErrorDeAplicacion as error:
        await db.rollback()
        await registrar(
            db,
            action=f"dispatch.{accion}.denied",
            resource_type="dispatch_request",
            resource_id=dispatch_id,
            actor_user_id=actor.user_id,
            company_id=empresa,
            outcome=Outcome.DENIED,
            reason=error.message,
            ip_address=_ip(request),
        )
        await db.commit()
        raise

    await registrar(
        db,
        action=f"dispatch.{accion}",
        resource_type="dispatch_request",
        resource_id=dispatch_id,
        actor_user_id=actor.user_id,
        company_id=empresa,
        before_data={"status": resultado.desde},
        after_data={"status": resultado.hacia},
        ip_address=_ip(request),
    )
    await db.commit()

    return AccionResponse(
        id=resultado.id,
        from_status=resultado.desde,
        to_status=resultado.hacia,
        row_version=resultado.row_version,
    )


@router.post("/{dispatch_id}/approve", response_model=AccionResponse)
async def aprobar(
    dispatch_id: UUID,
    datos: AccionRequest,
    request: Request,
    actor: ActorDep,
    db: SesionDb,
    redis: RedisDep,
) -> AccionResponse:
    permisos = await obtener_permisos_efectivos(db, redis, actor.user_id)
    return await _ejecutar_accion(
        db,
        request,
        actor,
        permisos,
        dispatch_id,
        permiso=Perm.DISPATCH_REQUESTS_APPROVE,
        accion="approved",
        ejecutar=lambda empresas: service.aprobar(
            db,
            dispatch_id=dispatch_id,
            actor_user_id=actor.user_id,
            permisos=permisos,
            company_ids=empresas,
            notas=datos.notes,
            row_version=datos.row_version,
        ),
    )


@router.post("/{dispatch_id}/reject", response_model=AccionResponse)
async def rechazar(
    dispatch_id: UUID,
    datos: RechazoRequest,
    request: Request,
    actor: ActorDep,
    db: SesionDb,
    redis: RedisDep,
) -> AccionResponse:
    permisos = await obtener_permisos_efectivos(db, redis, actor.user_id)
    return await _ejecutar_accion(
        db,
        request,
        actor,
        permisos,
        dispatch_id,
        permiso=Perm.DISPATCH_REQUESTS_REJECT,
        accion="rejected",
        ejecutar=lambda empresas: service.rechazar(
            db,
            dispatch_id=dispatch_id,
            actor_user_id=actor.user_id,
            permisos=permisos,
            company_ids=empresas,
            motivo=datos.reason,
            row_version=datos.row_version,
        ),
    )


@router.post("/{dispatch_id}/prepare", response_model=AccionResponse)
async def preparar(
    dispatch_id: UUID,
    datos: AccionRequest,
    request: Request,
    actor: ActorDep,
    db: SesionDb,
    redis: RedisDep,
) -> AccionResponse:
    permisos = await obtener_permisos_efectivos(db, redis, actor.user_id)
    return await _ejecutar_accion(
        db,
        request,
        actor,
        permisos,
        dispatch_id,
        permiso=Perm.DISPATCH_REQUESTS_PREPARE,
        accion="preparing",
        ejecutar=lambda empresas: service.preparar(
            db,
            dispatch_id=dispatch_id,
            actor_user_id=actor.user_id,
            permisos=permisos,
            company_ids=empresas,
            notas=datos.notes,
            row_version=datos.row_version,
        ),
    )


@router.post("/{dispatch_id}/complete", response_model=AccionResponse)
async def completar(
    dispatch_id: UUID,
    datos: AccionRequest,
    request: Request,
    actor: ActorDep,
    db: SesionDb,
    redis: RedisDep,
) -> AccionResponse:
    permisos = await obtener_permisos_efectivos(db, redis, actor.user_id)
    return await _ejecutar_accion(
        db,
        request,
        actor,
        permisos,
        dispatch_id,
        permiso=Perm.DISPATCH_REQUESTS_COMPLETE,
        accion="completed",
        ejecutar=lambda empresas: service.completar(
            db,
            dispatch_id=dispatch_id,
            actor_user_id=actor.user_id,
            permisos=permisos,
            company_ids=empresas,
            notas=datos.notes,
            row_version=datos.row_version,
        ),
    )


@router.post("/{dispatch_id}/cancel", response_model=AccionResponse)
async def cancelar(
    dispatch_id: UUID,
    datos: AccionRequest,
    request: Request,
    actor: ActorDep,
    db: SesionDb,
    redis: RedisDep,
) -> AccionResponse:
    """Cancela la solicitud (ADR-0013).

    El cliente solo puede antes de que Operaciones apruebe. Operaciones, hasta
    `PREPARING`. La restricción la aplica la política de dominio, no el permiso.
    """
    permisos = await obtener_permisos_efectivos(db, redis, actor.user_id)
    return await _ejecutar_accion(
        db,
        request,
        actor,
        permisos,
        dispatch_id,
        permiso=Perm.DISPATCH_REQUESTS_CANCEL,
        accion="cancelled",
        ejecutar=lambda empresas: service.cancelar(
            db,
            dispatch_id=dispatch_id,
            actor_user_id=actor.user_id,
            permisos=permisos,
            company_ids=empresas,
            motivo=datos.notes,
            row_version=datos.row_version,
        ),
    )

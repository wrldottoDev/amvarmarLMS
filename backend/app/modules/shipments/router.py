"""Transiciones y requisitos de carga (Paso 2.4)."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.errors import RecursoNoEncontrado, SinPermiso
from app.core.pagination import Cursor, normalizar_limite
from app.core.redis import get_redis
from app.modules.audit.models import Outcome
from app.modules.audit.service import registrar
from app.modules.auth.dependencies import Actor, actor_actual
from app.modules.rbac.service import obtener_permisos_efectivos
from app.modules.shipments import gestion, queries, service
from app.modules.shipments.models import (
    RequirementStatus,
    RequirementType,
    ShipmentStatus,
)

router = APIRouter(prefix="/api/v1/shipments", tags=["shipments"])

SesionDb = Annotated[AsyncSession, Depends(get_session)]
RedisDep = Annotated[Redis, Depends(get_redis)]
ActorDep = Annotated[Actor, Depends(actor_actual)]


class TransitionRequest(BaseModel):
    to_status: ShipmentStatus
    row_version: int = Field(ge=1)
    occurred_at: datetime | None = None
    note: str | None = Field(default=None, max_length=2000)
    location: str | None = Field(default=None, max_length=180)


class TransitionResponse(BaseModel):
    shipment_id: UUID
    from_status: str
    to_status: str
    row_version: int
    event_id: UUID


class RequirementRequest(BaseModel):
    requirement_type: RequirementType
    title: str = Field(min_length=1, max_length=180)
    required_from: str = Field(pattern="^(CLIENT|STAFF)$")
    document_type_id: UUID | None = None
    description: str | None = Field(default=None, max_length=2000)
    blocks_dispatch: bool = True
    due_at: datetime | None = None


class RequirementPatch(BaseModel):
    status: RequirementStatus
    reason: str | None = Field(default=None, max_length=2000)


class RequirementResponse(BaseModel):
    id: UUID
    shipment_id: UUID
    status: str


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _denegado(error: Exception) -> RecursoNoEncontrado:
    """404 y no 403 sobre un requisito ajeno.

    Confirmar que el requisito existe ya diría algo de la carga de otra
    empresa. Misma razón que en las transiciones.
    """
    return RecursoNoEncontrado("Requisito no encontrado.")


class CrearCargaRequest(BaseModel):
    """Alta de carga por Operaciones.

    No lleva estado: toda carga nace en `PRE_ALERT` y avanza por el motor de
    transiciones, que valida el catálogo y deja evento. Aceptar un estado acá
    sería una puerta de atrás alrededor de esas reglas.
    """

    company_id: UUID
    origin_location_id: UUID
    destination_location_id: UUID
    origin_facility_id: UUID | None = None
    destination_address: str | None = Field(default=None, max_length=240)
    description: str | None = Field(default=None, max_length=4000)
    transport_mode: str | None = Field(default=None, max_length=20)
    estimated_arrival_at: datetime | None = None
    weight_kg: Decimal | None = Field(default=None, ge=0)
    volumetric_weight_kg: Decimal | None = Field(default=None, ge=0)
    volume_m3: Decimal | None = Field(default=None, ge=0)
    permit_review_required: bool = False
    assigned_to: UUID | None = None


class CargaCreadaResponse(BaseModel):
    id: UUID
    shipment_number: str
    status: str
    row_version: int


class ActualizarCargaRequest(BaseModel):
    """Corrección de datos. Solo se toca lo que venga en el cuerpo.

    `row_version` es obligatorio: sin él, dos personas editando a la vez se
    pisan y la segunda gana en silencio.
    """

    row_version: int = Field(ge=1)

    origin_location_id: UUID | None = None
    origin_facility_id: UUID | None = None
    destination_location_id: UUID | None = None
    destination_address: str | None = Field(default=None, max_length=240)
    description: str | None = Field(default=None, max_length=4000)
    transport_mode: str | None = Field(default=None, max_length=20)
    estimated_arrival_at: datetime | None = None
    weight_kg: Decimal | None = Field(default=None, ge=0)
    volumetric_weight_kg: Decimal | None = Field(default=None, ge=0)
    volume_m3: Decimal | None = None
    permit_review_required: bool | None = None
    assigned_to: UUID | None = None


class CargaActualizadaResponse(BaseModel):
    id: UUID
    row_version: int


class UbicacionCatalogoResponse(BaseModel):
    id: UUID
    location_code: str
    name: str
    country_code: str


class BodegaResponse(BaseModel):
    id: UUID
    facility_code: str
    location_id: UUID
    uses_warehouse_receipt: bool


@router.get("/catalogos/locations", response_model=list[UbicacionCatalogoResponse])
async def listar_ubicaciones(actor: ActorDep, db: SesionDb) -> list[UbicacionCatalogoResponse]:
    """Catálogo de ubicaciones, para los selectores del formulario de alta.

    No lleva permiso propio: son puertos y ciudades, no información de ninguna
    empresa. Exige estar autenticado, como todo lo demás.
    """
    filas = (
        await db.execute(
            text("""
                SELECT id, location_code, name, country_code
                FROM locations WHERE is_active ORDER BY country_code, name
            """)
        )
    ).all()
    return [UbicacionCatalogoResponse(**dict(f._mapping)) for f in filas]


@router.get("/catalogos/facilities", response_model=list[BodegaResponse])
async def listar_bodegas(actor: ActorDep, db: SesionDb) -> list[BodegaResponse]:
    """Bodegas. `uses_warehouse_receipt` decide si la carga exigirá WR (ADR-0005)."""
    filas = (
        await db.execute(
            text("""
                SELECT id, facility_code, location_id, uses_warehouse_receipt
                FROM facilities WHERE is_active ORDER BY facility_code
            """)
        )
    ).all()
    return [BodegaResponse(**dict(f._mapping)) for f in filas]


@router.post("", response_model=CargaCreadaResponse, status_code=status.HTTP_201_CREATED)
async def crear_carga(
    datos: CrearCargaRequest,
    request: Request,
    actor: ActorDep,
    db: SesionDb,
    redis: RedisDep,
) -> CargaCreadaResponse:
    permisos = await obtener_permisos_efectivos(db, redis, actor.user_id)

    try:
        creada = await gestion.crear(
            db,
            datos=gestion.DatosDeCarga(
                company_id=datos.company_id,
                origin_location_id=datos.origin_location_id,
                destination_location_id=datos.destination_location_id,
                origin_facility_id=datos.origin_facility_id,
                destination_address=datos.destination_address,
                description=datos.description,
                transport_mode=datos.transport_mode,
                estimated_arrival_at=datos.estimated_arrival_at,
                weight_kg=datos.weight_kg,
                volumetric_weight_kg=datos.volumetric_weight_kg,
                volume_m3=datos.volume_m3,
                permit_review_required=datos.permit_review_required,
                assigned_to=datos.assigned_to,
            ),
            actor_user_id=actor.user_id,
            permisos=permisos,
        )
    except service.SinPermisoParaTransicion as error:
        # 403 y no 404: acá el actor eligió la empresa, así que no se le está
        # revelando la existencia de nada que no supiera.
        raise SinPermiso("No tiene permiso para crear cargas en esa empresa.") from error

    await registrar(
        db,
        action="shipment.created",
        resource_type="shipment",
        resource_id=creada.id,
        actor_user_id=actor.user_id,
        company_id=datos.company_id,
        after_data={"shipment_number": creada.shipment_number},
        ip_address=_ip(request),
    )
    await db.commit()

    return CargaCreadaResponse(**vars(creada))


@router.patch("/{shipment_id}", response_model=CargaActualizadaResponse)
async def actualizar_carga(
    shipment_id: UUID,
    datos: ActualizarCargaRequest,
    request: Request,
    actor: ActorDep,
    db: SesionDb,
    redis: RedisDep,
) -> CargaActualizadaResponse:
    permisos = await obtener_permisos_efectivos(db, redis, actor.user_id)

    # `exclude_unset` y no `exclude_none`: poner un campo en null es una
    # corrección legítima (borrar una ETA equivocada), y no debe confundirse
    # con no haberlo mandado.
    cambios = datos.model_dump(exclude_unset=True, exclude={"row_version"})

    try:
        nueva_version = await gestion.actualizar(
            db,
            shipment_id=shipment_id,
            cambios=cambios,
            row_version=datos.row_version,
            actor_user_id=actor.user_id,
            permisos=permisos,
        )
    except service.SinPermisoParaTransicion as error:
        raise RecursoNoEncontrado("Carga no encontrada.") from error

    await registrar(
        db,
        action="shipment.updated",
        resource_type="shipment",
        resource_id=shipment_id,
        actor_user_id=actor.user_id,
        after_data={"campos": sorted(cambios)},
        ip_address=_ip(request),
    )
    await db.commit()

    return CargaActualizadaResponse(id=shipment_id, row_version=nueva_version)


@router.post("/{shipment_id}/transitions", response_model=TransitionResponse)
async def transicionar(
    shipment_id: UUID,
    datos: TransitionRequest,
    request: Request,
    actor: ActorDep,
    db: SesionDb,
    redis: RedisDep,
) -> TransitionResponse:
    permisos = await obtener_permisos_efectivos(db, redis, actor.user_id)

    try:
        resultado = await service.transicionar(
            db,
            shipment_id=shipment_id,
            datos=service.DatosTransicion(
                to_status=datos.to_status.value,
                row_version=datos.row_version,
                occurred_at=datos.occurred_at,
                note=datos.note,
                location=datos.location,
            ),
            actor_user_id=actor.user_id,
            permisos=permisos,
        )
    except service.SinPermisoParaTransicion as error:
        # El intento denegado se audita y ese registro SÍ se confirma: es
        # información de seguridad, no un efecto de negocio.
        await _auditar_intento_fallido(
            db,
            request,
            actor=actor,
            shipment_id=shipment_id,
            motivo=f"Sin el permiso {error.args[0]}",
        )
        await db.commit()
        # 404 y no 403: confirmar que la carga existe pero no es suya ya es
        # información sobre otra empresa.
        raise RecursoNoEncontrado("Carga no encontrada.") from error
    except (service.TransicionInvalida, service.RequisitosPendientes) as error:
        await _auditar_intento_fallido(
            db, request, actor=actor, shipment_id=shipment_id, motivo=error.message
        )
        await db.commit()
        raise

    await registrar(
        db,
        action="shipment.status.changed",
        resource_type="shipment",
        resource_id=shipment_id,
        actor_user_id=actor.user_id,
        outcome=Outcome.SUCCESS,
        before_data={"status": resultado.desde},
        after_data={"status": resultado.hacia},
        reason=datos.note,
        ip_address=_ip(request),
    )
    await db.commit()

    return TransitionResponse(
        shipment_id=resultado.shipment_id,
        from_status=resultado.desde,
        to_status=resultado.hacia,
        row_version=resultado.row_version,
        event_id=resultado.evento_id,
    )


async def _auditar_intento_fallido(
    db: AsyncSession,
    request: Request,
    *,
    actor: Actor,
    shipment_id: UUID,
    motivo: str,
) -> None:
    """Un intento rechazado también deja rastro.

    Sin esto, una racha de transiciones denegadas —el patrón típico de alguien
    tanteando permisos— sería invisible en la bitácora.
    """
    await db.rollback()
    await registrar(
        db,
        action="shipment.status.change_denied",
        resource_type="shipment",
        resource_id=shipment_id,
        actor_user_id=actor.user_id,
        outcome=Outcome.DENIED,
        reason=motivo,
        ip_address=_ip(request),
    )


@router.post(
    "/{shipment_id}/requirements",
    response_model=RequirementResponse,
    status_code=status.HTTP_201_CREATED,
)
async def abrir_requisito(
    shipment_id: UUID,
    datos: RequirementRequest,
    request: Request,
    actor: ActorDep,
    db: SesionDb,
    redis: RedisDep,
) -> RequirementResponse:
    permisos = await obtener_permisos_efectivos(db, redis, actor.user_id)

    try:
        requirement_id = await service.abrir_requisito(
            db,
            shipment_id=shipment_id,
            requirement_type=datos.requirement_type.value,
            title=datos.title,
            required_from=datos.required_from,
            actor_user_id=actor.user_id,
            permisos=permisos,
            document_type_id=datos.document_type_id,
            description=datos.description,
            blocks_dispatch=datos.blocks_dispatch,
            due_at=datos.due_at,
        )
    except service.SinPermisoSobreRequisito as error:
        raise _denegado(error) from error

    await registrar(
        db,
        action="shipment.requirement.opened",
        resource_type="shipment_requirement",
        resource_id=requirement_id,
        actor_user_id=actor.user_id,
        after_data={"titulo": datos.title, "tipo": datos.requirement_type.value},
        ip_address=_ip(request),
    )
    await db.commit()

    return RequirementResponse(
        id=requirement_id,
        shipment_id=shipment_id,
        status=service.ESTADO_INICIAL[datos.requirement_type.value],
    )


@router.patch("/{shipment_id}/requirements/{requirement_id}", response_model=RequirementResponse)
async def resolver_requisito(
    shipment_id: UUID,
    requirement_id: UUID,
    datos: RequirementPatch,
    request: Request,
    actor: ActorDep,
    db: SesionDb,
    redis: RedisDep,
) -> RequirementResponse:
    permisos = await obtener_permisos_efectivos(db, redis, actor.user_id)

    try:
        resuelto_en = await service.resolver_requisito(
            db,
            requirement_id=requirement_id,
            nuevo_estado=datos.status.value,
            actor_user_id=actor.user_id,
            permisos=permisos,
            motivo=datos.reason,
        )
    except service.SinPermisoSobreRequisito as error:
        raise _denegado(error) from error

    if resuelto_en != shipment_id:
        # El requisito existe pero pertenece a otra carga.
        raise RecursoNoEncontrado("Requisito no encontrado.")

    await registrar(
        db,
        action="shipment.requirement.resolved",
        resource_type="shipment_requirement",
        resource_id=requirement_id,
        actor_user_id=actor.user_id,
        after_data={"estado": datos.status.value},
        reason=datos.reason,
        ip_address=_ip(request),
    )
    await db.commit()

    return RequirementResponse(
        id=requirement_id, shipment_id=shipment_id, status=datos.status.value
    )


# --- Lectura (Paso 2.5) ---


class UbicacionResponse(BaseModel):
    location_code: str
    name: str
    country_code: str


class ShipmentResumenResponse(BaseModel):
    """Resumen de carga para listados y dashboard.

    `status` y `open_requirements_count` son campos SEPARADOS a propósito:
    "faltan documentos" no reemplaza al estado logístico. La carga puede estar
    `IN_TRANSIT` con requisitos abiertos, y la interfaz muestra las dos cosas.
    """

    id: UUID
    shipment_number: str
    status: str
    open_requirements_count: int
    client_action_required_count: int
    # Referencia comercial principal. Puede faltar: el WR es opcional y la
    # factura no siempre existe todavía.
    invoice: str | None
    origin: UbicacionResponse
    destination: UbicacionResponse
    estimated_arrival_at: datetime | None
    current_location: str | None
    transport_mode: str | None
    package_count: int
    permit_review_required: bool
    created_at: datetime
    updated_at: datetime


class PaginaShipments(BaseModel):
    items: list[ShipmentResumenResponse]
    next_cursor: str | None
    has_more: bool


class EventoResponse(BaseModel):
    id: UUID
    event_type: str
    from_status: str | None
    to_status: str | None
    title: str
    description: str | None
    location: str | None
    occurred_at: datetime
    recorded_at: datetime
    actor: str | None


class PaginaTimeline(BaseModel):
    items: list[EventoResponse]
    next_cursor: str | None
    has_more: bool


def _a_resumen(fila: Any) -> ShipmentResumenResponse:
    # `Any` es honesto: la fila viene de SQL crudo, no de un modelo mapeado.
    return ShipmentResumenResponse(
        id=fila.id,
        shipment_number=fila.shipment_number,
        status=fila.current_status_code,
        open_requirements_count=fila.requisitos_abiertos,
        client_action_required_count=fila.requisitos_del_cliente,
        invoice=fila.factura,
        origin=UbicacionResponse(
            location_code=fila.origen_codigo,
            name=fila.origen_nombre,
            country_code=fila.origen_pais,
        ),
        destination=UbicacionResponse(
            location_code=fila.destino_codigo,
            name=fila.destino_nombre,
            country_code=fila.destino_pais,
        ),
        estimated_arrival_at=fila.estimated_arrival_at,
        current_location=fila.current_location,
        transport_mode=fila.transport_mode,
        package_count=fila.package_count,
        permit_review_required=fila.permit_review_required,
        created_at=fila.created_at,
        updated_at=fila.updated_at,
    )


@router.get("", response_model=PaginaShipments)
async def listar_shipments(
    actor: ActorDep,
    db: SesionDb,
    redis: RedisDep,
    limit: Annotated[int | None, Query(ge=1)] = None,
    cursor: str | None = None,
    status_filtro: Annotated[list[ShipmentStatus] | None, Query(alias="status")] = None,
    company_id: UUID | None = None,
    eta_from: datetime | None = None,
    eta_to: datetime | None = None,
    q: Annotated[str | None, Query(max_length=180)] = None,
    archived: bool = False,
) -> PaginaShipments:
    """Listado con filtros y paginación por cursor.

    El `limit` se recorta al máximo del servidor en vez de rechazarse: pedir
    10000 devuelve 100, no un error.
    """
    permisos = await obtener_permisos_efectivos(db, redis, actor.user_id)

    pagina = await queries.listar_shipments(
        db,
        permisos=permisos,
        filtros=queries.FiltrosListado(
            estados=[e.value for e in status_filtro] if status_filtro else None,
            company_id=company_id,
            eta_desde=eta_from,
            eta_hasta=eta_to,
            texto=q,
            incluir_archivadas=archived,
        ),
        limite=normalizar_limite(limit),
        cursor=Cursor.decodificar(cursor) if cursor else None,
    )

    return PaginaShipments(
        items=[_a_resumen(f) for f in pagina.items],
        next_cursor=pagina.next_cursor,
        has_more=pagina.has_more,
    )


class ShipmentDetalleResponse(ShipmentResumenResponse):
    row_version: int
    description: str | None
    destination_address: str | None
    received_at: datetime | None
    stored_at: datetime | None
    dispatched_at: datetime | None
    delivered_at: datetime | None


@router.get("/{shipment_id}", response_model=ShipmentDetalleResponse)
async def obtener_shipment(
    shipment_id: UUID, actor: ActorDep, db: SesionDb, redis: RedisDep
) -> ShipmentDetalleResponse:
    permisos = await obtener_permisos_efectivos(db, redis, actor.user_id)
    fila = await queries.obtener_shipment(db, shipment_id=shipment_id, permisos=permisos)

    return ShipmentDetalleResponse(
        **_a_resumen(fila).model_dump(),
        row_version=fila.row_version,
        description=fila.description,
        destination_address=fila.destination_address,
        received_at=fila.received_at,
        stored_at=fila.stored_at,
        dispatched_at=fila.dispatched_at,
        delivered_at=fila.delivered_at,
    )


@router.get("/{shipment_id}/timeline", response_model=PaginaTimeline)
async def obtener_timeline(
    shipment_id: UUID,
    actor: ActorDep,
    db: SesionDb,
    redis: RedisDep,
    limit: Annotated[int | None, Query(ge=1)] = None,
    cursor: str | None = None,
) -> PaginaTimeline:
    permisos = await obtener_permisos_efectivos(db, redis, actor.user_id)
    pagina = await queries.timeline(
        db,
        shipment_id=shipment_id,
        permisos=permisos,
        limite=normalizar_limite(limit),
        cursor=Cursor.decodificar(cursor) if cursor else None,
    )

    return PaginaTimeline(
        items=[
            EventoResponse(
                id=f.id,
                event_type=f.event_type,
                from_status=f.from_status_code,
                to_status=f.to_status_code,
                title=f.title,
                description=f.description,
                location=f.location,
                occurred_at=f.occurred_at,
                recorded_at=f.recorded_at,
                actor=(f"{f.first_name} {f.last_name}" if f.first_name else None),
            )
            for f in pagina.items
        ],
        next_cursor=pagina.next_cursor,
        has_more=pagina.has_more,
    )

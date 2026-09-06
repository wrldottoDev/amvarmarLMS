"""Endpoints de documentos (Paso 3.1).

Subida en dos tiempos: `presign` reserva y firma, el cliente sube directo al
storage, `complete` verifica lo que realmente llegó. La descarga siempre emite
una URL firmada de corta duración; nunca se expone la `storage_key`.
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.core.database import get_session
from app.core.errors import ErrorDeAplicacion, RecursoNoEncontrado, SinPermiso
from app.core.redis import get_redis
from app.infrastructure.storage.s3 import TTL_DESCARGA_SEGUNDOS, TTL_SUBIDA_SEGUNDOS
from app.modules.audit.models import Outcome
from app.modules.audit.service import registrar
from app.modules.auth.dependencies import Actor, actor_actual
from app.modules.documents import service
from app.modules.documents.models import DocumentContext, IssuedBy
from app.modules.rbac.catalog import Perm
from app.modules.rbac.service import PermisosEfectivos, obtener_permisos_efectivos
from app.modules.shipments.queries import alcance_de_lectura

router = APIRouter(prefix="/api/v1", tags=["documents"])

SesionDb = Annotated[AsyncSession, Depends(get_session)]
RedisDep = Annotated[Redis, Depends(get_redis)]
ActorDep = Annotated[Actor, Depends(actor_actual)]


class PresignRequest(BaseModel):
    document_type_id: UUID
    issued_by: IssuedBy
    # El nombre original se conserva para mostrárselo al usuario; el que se usa
    # al servir se saneó aparte (`safe_name`).
    original_name: str = Field(min_length=1, max_length=255)


class PresignResponse(BaseModel):
    """El cliente sube con `PUT` a `upload_url` y después llama a `complete`.

    `storage_key` NO se devuelve: es detalle interno y exponerlo daría una pista
    de la estructura del bucket.
    """

    document_id: UUID
    upload_url: str
    expires_in_seconds: int
    max_bytes: int


class CompleteRequest(BaseModel):
    document_id: UUID


class CompleteResponse(BaseModel):
    document_id: UUID
    upload_status: str


class DownloadResponse(BaseModel):
    url: str
    filename: str
    media_type: str
    expires_in_seconds: int


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _encolar_documento(document_id: UUID) -> None:
    # Import local para evitar cargar Celery durante la generación de OpenAPI.
    from app.workers.tasks.documents import encolar_procesamiento

    encolar_procesamiento(document_id)


async def _confirmar_y_encolar(
    db: AsyncSession,
    *,
    document_id: UUID,
) -> None:
    """Publica después del commit y revierte PROCESSING si el broker no acepta."""
    await db.commit()
    try:
        _encolar_documento(document_id)
    except Exception as error:
        await db.execute(
            text("""
                UPDATE documents SET upload_status = 'UPLOADING'
                WHERE id = :id AND upload_status = 'PROCESSING'
            """),
            {"id": document_id},
        )
        await db.commit()
        raise ErrorDeAplicacion(
            "No fue posible iniciar el procesamiento. Intente completar la subida nuevamente.",
            code="DOCUMENT_PROCESSING_UNAVAILABLE",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from error


async def _empresa_de_la_carga(
    db: AsyncSession, shipment_id: UUID, permisos: PermisosEfectivos
) -> UUID:
    """Empresa dueña de la carga, verificando que el actor la alcance.

    El filtro por alcance va en el WHERE: si la carga es de otra empresa, la
    consulta no devuelve nada y el actor recibe 404, indistinguible de que la
    carga no exista.
    """
    alcance = alcance_de_lectura(permisos)
    if alcance.no_ve_nada:
        raise RecursoNoEncontrado("Carga no encontrada.")

    condiciones = ["id = :shipment_id", "deleted_at IS NULL"]
    parametros: dict[str, object] = {"shipment_id": shipment_id}
    if not alcance.global_:
        condiciones.append("company_id = ANY(:empresas)")
        parametros["empresas"] = alcance.company_ids

    consulta = f"SELECT company_id FROM shipments WHERE {' AND '.join(condiciones)}"  # noqa: S608
    company_id = (await db.execute(text(consulta), parametros)).scalar_one_or_none()

    if company_id is None:
        raise RecursoNoEncontrado("Carga no encontrada.")

    return UUID(str(company_id))


@router.post(
    "/shipments/{shipment_id}/documents/presign",
    response_model=PresignResponse,
    status_code=status.HTTP_201_CREATED,
)
async def preparar_subida(
    shipment_id: UUID,
    datos: PresignRequest,
    request: Request,
    actor: ActorDep,
    db: SesionDb,
    redis: RedisDep,
) -> PresignResponse:
    """Primer tiempo: reserva el documento y devuelve una URL firmada.

    El documento queda en `UPLOADING`: existe pero no es descargable ni
    satisface ningún requisito hasta que `complete` verifique los bytes.
    """
    permisos = await obtener_permisos_efectivos(db, redis, actor.user_id)
    company_id = await _empresa_de_la_carga(db, shipment_id, permisos)

    subida = await service.preparar_subida(
        db,
        shipment_id=shipment_id,
        document_type_id=datos.document_type_id,
        issued_by=datos.issued_by.value,
        original_name=datos.original_name,
        company_id=company_id,
        actor_user_id=actor.user_id,
        permisos=permisos,
    )

    await registrar(
        db,
        action="document.upload.presigned",
        resource_type="document",
        resource_id=subida.document_id,
        actor_user_id=actor.user_id,
        company_id=company_id,
        # El nombre original sí; la storage_key no — la redacción de auditoría
        # ya la filtraría, pero además no aporta nada al registro.
        after_data={"original_name": datos.original_name, "shipment_id": str(shipment_id)},
        ip_address=_ip(request),
    )
    await db.commit()

    return PresignResponse(
        document_id=subida.document_id,
        upload_url=subida.upload_url,
        expires_in_seconds=TTL_SUBIDA_SEGUNDOS,
        max_bytes=subida.max_bytes,
    )


@router.post(
    "/shipments/{shipment_id}/documents/complete",
    response_model=CompleteResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def completar_subida(
    shipment_id: UUID,
    datos: CompleteRequest,
    request: Request,
    actor: ActorDep,
    db: SesionDb,
    redis: RedisDep,
) -> CompleteResponse:
    """Valida metadata y cabecera; el worker calcula el hash por bloques.

    Si algo falla, el documento queda en `FAILED`, el objeto se borra y el
    intento queda auditado: una subida rechazada suele ser un error del cliente,
    pero una racha de ellas es otra cosa.
    """
    permisos = await obtener_permisos_efectivos(db, redis, actor.user_id)
    company_id = await _empresa_de_la_carga(db, shipment_id, permisos)

    try:
        resultado = await service.preparar_procesamiento(
            db,
            document_id=datos.document_id,
            company_id=company_id,
            context=DocumentContext.SHIPMENT.value,
            resource_id=shipment_id,
        )
    except ErrorDeAplicacion as error:
        # Solo errores de dominio: un fallo de infraestructura dejaría la
        # sesión inutilizable y `registrar` fallaría también.
        await registrar(
            db,
            action="document.upload.rejected",
            resource_type="document",
            resource_id=datos.document_id,
            actor_user_id=actor.user_id,
            company_id=company_id,
            outcome=Outcome.FAILED,
            reason=error.message,
            ip_address=_ip(request),
        )
        # El rechazo y la marca de FAILED sí se confirman.
        await db.commit()
        raise

    await registrar(
        db,
        action="document.processing.queued",
        resource_type="document",
        resource_id=datos.document_id,
        actor_user_id=actor.user_id,
        company_id=company_id,
        after_data={"upload_status": resultado.upload_status},
        ip_address=_ip(request),
    )
    await _confirmar_y_encolar(db, document_id=datos.document_id)

    return CompleteResponse(
        document_id=resultado.document_id,
        upload_status=resultado.upload_status,
    )


@router.get("/documents/{document_id}/download", response_model=DownloadResponse)
async def preparar_descarga(
    document_id: UUID,
    request: Request,
    actor: ActorDep,
    db: SesionDb,
    redis: RedisDep,
) -> DownloadResponse:
    """Emite una URL firmada de corta duración.

    Se devuelve la URL en el cuerpo en vez de redirigir para que el cliente
    pueda decidir qué hacer con ella (abrir, descargar, previsualizar) y para
    que la URL no quede en el historial de navegación como un redirect.
    """
    permisos = await obtener_permisos_efectivos(db, redis, actor.user_id)
    alcance = alcance_de_lectura(permisos)

    if alcance.no_ve_nada:
        raise RecursoNoEncontrado("Documento no encontrado.")

    descarga = await service.preparar_descarga(
        db,
        document_id=document_id,
        # `None` = alcance global; el servicio omite el filtro por empresa.
        company_ids=None if alcance.global_ else alcance.company_ids,
    )

    await registrar(
        db,
        action="document.downloaded",
        resource_type="document",
        resource_id=document_id,
        actor_user_id=actor.user_id,
        ip_address=_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()

    return DownloadResponse(
        url=descarga.url,
        filename=descarga.nombre_archivo,
        media_type=descarga.media_type,
        expires_in_seconds=TTL_DESCARGA_SEGUNDOS,
    )


class RequisitoResponse(BaseModel):
    """Un documento que la carga necesita, con su estado actual."""

    id: UUID
    document_type_id: UUID | None
    code: str | None
    label: str
    description: str | None
    status: str
    required_from: str
    blocks_dispatch: bool
    allowed_formats: list[str]
    # El documento ya subido para este requisito, si lo hay.
    document_id: UUID | None


class DocumentoResponse(BaseModel):
    id: UUID
    document_type_code: str
    document_type_label: str
    original_name: str
    media_type: str
    size_bytes: int
    upload_status: str
    created_at: datetime


class TipoDocumentoResponse(BaseModel):
    id: UUID
    code: str
    label: str
    description: str | None
    provided_by: str
    context: str
    issued_by_options: list[str]
    allowed_formats: list[str]


class ExpedienteResponse(BaseModel):
    """Todo lo documental de una carga en una sola respuesta.

    Va junto y no en tres llamadas: la pantalla necesita las tres cosas a la vez
    para poder decir qué falta, qué hay y qué se puede subir.
    """

    requisitos: list[RequisitoResponse]
    documentos: list[DocumentoResponse]
    tipos: list[TipoDocumentoResponse]


@router.get("/shipments/{shipment_id}/documents", response_model=ExpedienteResponse)
async def expediente_de_carga(
    shipment_id: UUID,
    actor: ActorDep,
    db: SesionDb,
    redis: RedisDep,
) -> ExpedienteResponse:
    permisos = await obtener_permisos_efectivos(db, redis, actor.user_id)
    alcance = alcance_de_lectura(permisos)

    if alcance.no_ve_nada:
        # 404 y no 403: confirmar que la carga existe ya es información.
        raise RecursoNoEncontrado("Carga no encontrada.")

    company_id = await _empresa_de_la_carga(db, shipment_id, permisos)
    datos = await service.expediente(
        db,
        shipment_id=shipment_id,
        company_ids=None if alcance.global_ else alcance.company_ids,
    )
    tipos = await service.tipos_de_documento(
        db,
        context=DocumentContext.SHIPMENT.value,
        permisos=permisos,
        company_id=company_id,
    )

    return ExpedienteResponse(
        requisitos=[RequisitoResponse(**vars(r)) for r in datos.requisitos],
        documentos=[DocumentoResponse(**vars(d)) for d in datos.documentos],
        tipos=[
            TipoDocumentoResponse(
                id=t.id,
                code=t.code,
                label=t.label,
                description=t.description,
                provided_by=t.provided_by,
                context=t.context,
                issued_by_options=list(t.issued_by_options),
                allowed_formats=list(t.allowed_formats),
            )
            for t in tipos
        ],
    )


@router.get("/document-types", response_model=list[TipoDocumentoResponse])
async def catalogo_de_tipos_documentales(
    context: DocumentContext,
    actor: ActorDep,
    db: SesionDb,
    redis: RedisDep,
) -> list[TipoDocumentoResponse]:
    permisos = await obtener_permisos_efectivos(db, redis, actor.user_id)
    tipos = await service.tipos_de_documento(db, context=context.value, permisos=permisos)
    return [
        TipoDocumentoResponse(
            id=t.id,
            code=t.code,
            label=t.label,
            description=t.description,
            provided_by=t.provided_by,
            context=t.context,
            issued_by_options=list(t.issued_by_options),
            allowed_formats=list(t.allowed_formats),
        )
        for t in tipos
    ]


class RenombrarDocumentoRequest(BaseModel):
    original_name: str = Field(min_length=1, max_length=255)


class DocumentoRenombradoResponse(BaseModel):
    original_name: str


@router.patch("/documents/{document_id}", response_model=DocumentoRenombradoResponse)
async def renombrar_documento(
    document_id: UUID,
    datos: RenombrarDocumentoRequest,
    request: Request,
    actor: ActorDep,
    db: SesionDb,
    redis: RedisDep,
) -> DocumentoRenombradoResponse:
    """Corrige el nombre visible de un documento.

    Es la acción `rename` de `edit_files` del sistema viejo. Solo personal
    interno: el nombre es cómo Operaciones y el agente aduanal encuentran el
    papel, y dejar que cada cliente lo cambie convierte el expediente en algo
    que solo entiende quien lo tocó último.
    """
    permisos = await obtener_permisos_efectivos(db, redis, actor.user_id)

    if not any(p.code == Perm.DOCUMENTS_UPLOAD_INTERNAL for p in permisos.permisos):
        raise SinPermiso("No tiene permiso para renombrar documentos.")

    alcance = alcance_de_lectura(permisos)
    if alcance.no_ve_nada:
        raise RecursoNoEncontrado("Documento no encontrado.")

    nombre = await service.renombrar(
        db,
        document_id=document_id,
        nuevo_nombre=datos.original_name,
        company_ids=None if alcance.global_ else alcance.company_ids,
    )

    await registrar(
        db,
        action="document.renamed",
        resource_type="document",
        resource_id=document_id,
        actor_user_id=actor.user_id,
        after_data={"original_name": nombre},
        ip_address=_ip(request),
    )
    await db.commit()

    return DocumentoRenombradoResponse(original_name=nombre)


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def invalidar_documento(
    document_id: UUID,
    request: Request,
    actor: ActorDep,
    db: SesionDb,
    redis: RedisDep,
    motivo: Annotated[str, Query(min_length=3, max_length=1000)],
) -> None:
    """Saca un documento del expediente.

    Es la acción `delete` de `edit_files` del sistema viejo, con una diferencia:
    el objeto NO se borra del storage. Un archivo que alguien subió y otro quitó
    puede ser evidencia de un error o de algo peor, y pesa mucho menos que la
    posibilidad de tener que reconstruir qué pasó.

    Si el documento satisfacía un requisito y no queda otro de su tipo, ese
    requisito vuelve a pendiente: dejarlo por cumplido con el archivo fuera
    haría que la carga pasara a despacho sin el papel que la habilita.
    """
    permisos = await obtener_permisos_efectivos(db, redis, actor.user_id)

    if not any(p.code == Perm.DOCUMENTS_INVALIDATE for p in permisos.permisos):
        raise SinPermiso("No tiene permiso para quitar documentos del expediente.")

    alcance = alcance_de_lectura(permisos)
    if alcance.no_ve_nada:
        raise RecursoNoEncontrado("Documento no encontrado.")

    resultado = await service.invalidar(
        db,
        document_id=document_id,
        company_ids=None if alcance.global_ else alcance.company_ids,
        actor_user_id=actor.user_id,
        motivo=motivo,
    )

    await registrar(
        db,
        action="document.invalidated",
        resource_type="document",
        resource_id=document_id,
        actor_user_id=actor.user_id,
        after_data={
            "context": resultado.context,
            "resource_id": str(resultado.resource_id),
        },
        reason=motivo,
        ip_address=_ip(request),
    )
    await db.commit()


@router.get("/shipments/{shipment_id}/documents/download-all")
async def descargar_todos(
    shipment_id: UUID,
    request: Request,
    actor: ActorDep,
    db: SesionDb,
    redis: RedisDep,
) -> Response:
    """Todos los documentos de una carga en un ZIP.

    Existía en el sistema viejo y se usa para mandarle el expediente completo a
    un agente aduanal. Bajar ocho archivos uno por uno es trabajo que la máquina
    puede hacer.

    A diferencia de la descarga individual, el archivo pasa por la aplicación:
    hay que leer cada objeto para comprimirlo, y no se puede firmar una URL de
    algo que todavía no existe. Por eso se audita: es la única vía por la que
    salen varios documentos de una sola vez.
    """
    permisos = await obtener_permisos_efectivos(db, redis, actor.user_id)
    alcance = alcance_de_lectura(permisos)

    if alcance.no_ve_nada:
        raise RecursoNoEncontrado("Carga no encontrada.")

    nombre, contenido = await service.paquete_de_documentos(
        db,
        shipment_id=shipment_id,
        company_ids=None if alcance.global_ else alcance.company_ids,
    )

    await registrar(
        db,
        action="shipment.documents.bulk_download",
        resource_type="shipment",
        resource_id=shipment_id,
        actor_user_id=actor.user_id,
        outcome=Outcome.SUCCESS,
        after_data={"bytes": len(contenido)},
        ip_address=request.client.host if request.client else None,
    )
    await db.commit()

    return Response(
        content=contenido,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )


class DocumentoDespachoResponse(BaseModel):
    id: UUID
    document_type_code: str
    document_type_label: str
    original_name: str
    media_type: str
    size_bytes: int
    upload_status: str
    created_at: datetime


@router.get(
    "/dispatch-requests/{dispatch_id}/documents",
    response_model=list[DocumentoDespachoResponse],
)
async def documentos_de_despacho(
    dispatch_id: UUID, actor: ActorDep, db: SesionDb, redis: RedisDep
) -> list[DocumentoDespachoResponse]:
    """El BL y las facturas que cuelgan de la solicitud, no de una carga suelta."""
    permisos = await obtener_permisos_efectivos(db, redis, actor.user_id)
    alcance = alcance_de_lectura(permisos)

    if alcance.no_ve_nada:
        raise RecursoNoEncontrado("Solicitud de despacho no encontrada.")

    documentos = await service.documentos_de_despacho(
        db,
        dispatch_id=dispatch_id,
        company_ids=None if alcance.global_ else alcance.company_ids,
    )
    return [DocumentoDespachoResponse(**vars(d)) for d in documentos]


@router.post(
    "/dispatch-requests/{dispatch_id}/documents/presign",
    response_model=PresignResponse,
    status_code=status.HTTP_201_CREATED,
)
async def presign_documento_de_despacho(
    dispatch_id: UUID,
    datos: PresignRequest,
    request: Request,
    actor: ActorDep,
    db: SesionDb,
    redis: RedisDep,
) -> PresignResponse:
    """Adjunta un BL o una factura a la solicitud.

    Pasa por el mismo flujo de dos tiempos que cualquier documento: el formato,
    el tamaño y el escaneo se comportan igual venga de donde venga.
    """
    permisos = await obtener_permisos_efectivos(db, redis, actor.user_id)
    alcance = alcance_de_lectura(permisos)

    if alcance.no_ve_nada:
        raise RecursoNoEncontrado("Solicitud de despacho no encontrada.")

    preparada = await service.preparar_subida_de_despacho(
        db,
        dispatch_id=dispatch_id,
        document_type_id=datos.document_type_id,
        issued_by=datos.issued_by.value,
        original_name=datos.original_name,
        company_ids=None if alcance.global_ else alcance.company_ids,
        actor_user_id=actor.user_id,
        permisos=permisos,
    )

    await registrar(
        db,
        action="dispatch.document.presigned",
        resource_type="dispatch_request",
        resource_id=dispatch_id,
        actor_user_id=actor.user_id,
        after_data={"document_id": str(preparada.document_id)},
        ip_address=request.client.host if request.client else None,
    )
    await db.commit()

    return PresignResponse(
        document_id=preparada.document_id,
        upload_url=preparada.upload_url,
        expires_in_seconds=TTL_SUBIDA_SEGUNDOS,
        max_bytes=preparada.max_bytes,
    )


@router.post(
    "/dispatch-requests/{dispatch_id}/documents/complete",
    response_model=CompleteResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def completar_documento_de_despacho(
    dispatch_id: UUID,
    datos: CompleteRequest,
    request: Request,
    actor: ActorDep,
    db: SesionDb,
    redis: RedisDep,
) -> CompleteResponse:
    permisos = await obtener_permisos_efectivos(db, redis, actor.user_id)
    alcance = alcance_de_lectura(permisos)
    if alcance.no_ve_nada:
        raise RecursoNoEncontrado("Solicitud de despacho no encontrada.")

    company_id = await service.empresa_de_despacho_visible(
        db,
        dispatch_id=dispatch_id,
        company_ids=None if alcance.global_ else alcance.company_ids,
    )
    try:
        resultado = await service.preparar_procesamiento(
            db,
            document_id=datos.document_id,
            company_id=company_id,
            context=DocumentContext.DISPATCH.value,
            resource_id=dispatch_id,
        )
    except ErrorDeAplicacion as error:
        await registrar(
            db,
            action="dispatch.document.upload.rejected",
            resource_type="document",
            resource_id=datos.document_id,
            actor_user_id=actor.user_id,
            company_id=company_id,
            outcome=Outcome.FAILED,
            reason=error.message,
            ip_address=_ip(request),
        )
        await db.commit()
        raise

    await registrar(
        db,
        action="dispatch.document.processing.queued",
        resource_type="document",
        resource_id=datos.document_id,
        actor_user_id=actor.user_id,
        company_id=company_id,
        after_data={"dispatch_id": str(dispatch_id)},
        ip_address=_ip(request),
    )
    await _confirmar_y_encolar(db, document_id=datos.document_id)
    return CompleteResponse(
        document_id=resultado.document_id,
        upload_status=resultado.upload_status,
    )


@router.get("/dispatch-requests/{dispatch_id}/documents/bls")
async def descargar_bls(
    dispatch_id: UUID,
    request: Request,
    actor: ActorDep,
    db: SesionDb,
    redis: RedisDep,
) -> Response:
    """Todos los Bills of Lading del despacho en un ZIP.

    Un despacho puede llevar varios y el cliente los necesita juntos para su
    agente aduanal.
    """
    permisos = await obtener_permisos_efectivos(db, redis, actor.user_id)
    alcance = alcance_de_lectura(permisos)

    if alcance.no_ve_nada:
        raise RecursoNoEncontrado("Solicitud de despacho no encontrada.")

    nombre, contenido = await service.paquete_de_bls(
        db,
        dispatch_id=dispatch_id,
        company_ids=None if alcance.global_ else alcance.company_ids,
    )

    await registrar(
        db,
        action="dispatch.documents.bulk_download",
        resource_type="dispatch_request",
        resource_id=dispatch_id,
        actor_user_id=actor.user_id,
        outcome=Outcome.SUCCESS,
        ip_address=request.client.host if request.client else None,
    )
    await db.commit()

    return Response(
        content=contenido,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )

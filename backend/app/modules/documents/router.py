"""Endpoints de documentos (Paso 3.1).

Subida en dos tiempos: `presign` reserva y firma, el cliente sube directo al
storage, `complete` verifica lo que realmente llegó. La descarga siempre emite
una URL firmada de corta duración; nunca se expone la `storage_key`.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.core.database import get_session
from app.core.errors import ErrorDeAplicacion, RecursoNoEncontrado
from app.core.redis import get_redis
from app.infrastructure.storage.s3 import TTL_DESCARGA_SEGUNDOS, TTL_SUBIDA_SEGUNDOS
from app.modules.audit.models import Outcome
from app.modules.audit.service import registrar
from app.modules.auth.dependencies import Actor, actor_actual
from app.modules.documents import service
from app.modules.rbac.catalog import Perm
from app.modules.rbac.service import PermisosEfectivos, obtener_permisos_efectivos
from app.modules.shipments.queries import alcance_de_lectura

router = APIRouter(prefix="/api/v1", tags=["documents"])

SesionDb = Annotated[AsyncSession, Depends(get_session)]
RedisDep = Annotated[Redis, Depends(get_redis)]
ActorDep = Annotated[Actor, Depends(actor_actual)]


class PresignRequest(BaseModel):
    document_type_id: UUID
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
    media_type: str
    size_bytes: int
    sha256: str
    upload_status: str
    scan_status: str


class DownloadResponse(BaseModel):
    url: str
    filename: str
    media_type: str
    expires_in_seconds: int


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


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

    # Cualquiera de los dos permisos habilita subir: el cliente sube los suyos,
    # Operaciones además los internos (packing list, BL).
    if not (
        permisos.permite(Perm.DOCUMENTS_UPLOAD_CLIENT, company_id=company_id)
        or permisos.permite(Perm.DOCUMENTS_UPLOAD_INTERNAL, company_id=company_id)
    ):
        raise RecursoNoEncontrado("Carga no encontrada.")

    subida = await service.preparar_subida(
        db,
        shipment_id=shipment_id,
        document_type_id=datos.document_type_id,
        original_name=datos.original_name,
        company_id=company_id,
        actor_user_id=actor.user_id,
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


@router.post("/shipments/{shipment_id}/documents/complete", response_model=CompleteResponse)
async def completar_subida(
    shipment_id: UUID,
    datos: CompleteRequest,
    request: Request,
    actor: ActorDep,
    db: SesionDb,
    redis: RedisDep,
) -> CompleteResponse:
    """Segundo tiempo: verifica lo que realmente se subió.

    Si algo falla, el documento queda en `FAILED`, el objeto se borra y el
    intento queda auditado: una subida rechazada suele ser un error del cliente,
    pero una racha de ellas es otra cosa.
    """
    permisos = await obtener_permisos_efectivos(db, redis, actor.user_id)
    company_id = await _empresa_de_la_carga(db, shipment_id, permisos)

    try:
        resultado = await service.completar(
            db, document_id=datos.document_id, company_id=company_id
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
        # El rechazo y la marca de FAILED sí se confirman; el archivo ya se
        # borró del storage dentro del servicio.
        await db.commit()
        raise

    await registrar(
        db,
        action="document.upload.completed",
        resource_type="document",
        resource_id=datos.document_id,
        actor_user_id=actor.user_id,
        company_id=company_id,
        after_data={
            "media_type": resultado.media_type,
            "size_bytes": resultado.size_bytes,
            # El SHA-256 es la huella que permite verificar que el archivo no
            # cambió después. No es un secreto.
            "sha256": resultado.sha256,
        },
        ip_address=_ip(request),
    )
    await db.commit()

    return CompleteResponse(
        document_id=resultado.document_id,
        media_type=resultado.media_type,
        size_bytes=resultado.size_bytes,
        sha256=resultado.sha256,
        upload_status="PROCESSING",
        # El antivirus (Paso 3.2) corre después; hasta entonces no se descarga.
        scan_status="PENDING",
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

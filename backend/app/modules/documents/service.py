"""Subida en dos tiempos y descarga autorizada (Paso 3.1).

El archivo nunca pasa por la aplicación: el cliente sube directo al storage con
una URL firmada. Subir 250 MB a través de FastAPI ocuparía un worker durante
toda la transferencia.

A cambio, el servidor no ve los bytes mientras suben — por eso `completar()`
verifica sobre lo ya almacenado y no sobre lo que el cliente declaró.
"""

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflicto, RecursoNoEncontrado, ReglaDeNegocioViolada
from app.core.observability import metrics
from app.infrastructure.storage import s3
from app.modules.documents.models import DocumentContext, ProvidedBy, UploadStatus
from app.modules.documents.validation import (
    ArchivoInvalido,
    FormatoNoPermitido,
    nombre_seguro,
    validar_contenido,
    validar_extension,
    validar_tamano,
)
from app.modules.rbac.catalog import Perm
from app.modules.rbac.service import PermisosEfectivos
from app.modules.shipments.models import RequirementStatus
from app.modules.shipments.service import CargaArchivada

# Límites por defecto (ADR-0009). Se sobreescriben desde `system_settings`, que
# SUPER_ADMIN puede editar sin desplegar.
LIMITES_POR_DEFECTO: dict[str, int] = {
    "upload.max_size.pdf": 250 * 1024 * 1024,
    "upload.max_size.image": 100 * 1024 * 1024,
    "upload.max_size.other": 100 * 1024 * 1024,
    "upload.max_size.batch": 1024 * 1024 * 1024,
}

_FORMATOS_IMAGEN = frozenset({"JPG", "JPEG", "PNG", "WEBP", "HEIC"})


class DocumentoNoDisponible(Conflicto):
    """Existe pero todavía no se puede descargar: la subida no terminó."""

    code = "DOCUMENTO_NO_DISPONIBLE"


@dataclass(frozen=True)
class SubidaPreparada:
    document_id: UUID
    storage_key: str
    upload_url: str
    expira_en: datetime
    max_bytes: int


async def _limite_para(session: AsyncSession, formato: str) -> int:
    if formato == "PDF":
        clave = "upload.max_size.pdf"
    elif formato in _FORMATOS_IMAGEN:
        clave = "upload.max_size.image"
    else:
        clave = "upload.max_size.other"

    valor = (
        await session.execute(
            text("SELECT value FROM system_settings WHERE key = :k"), {"k": clave}
        )
    ).scalar_one_or_none()

    if valor is None:
        return LIMITES_POR_DEFECTO[clave]

    return (
        int(valor if isinstance(valor, int) else valor.get("bytes", 0))
        or LIMITES_POR_DEFECTO[clave]
    )


async def _tipo_de_documento(session: AsyncSession, document_type_id: UUID) -> Any:
    fila = (
        await session.execute(
            text("""
                SELECT id, code, allowed_formats, provided_by, context,
                       issued_by_options
                FROM document_types WHERE id = :id AND is_active
            """),
            {"id": document_type_id},
        )
    ).one_or_none()

    if fila is None:
        raise RecursoNoEncontrado("Tipo de documento no encontrado.")

    return fila


def _tiene_codigo(permisos: PermisosEfectivos, code: str) -> bool:
    return any(permiso.code == code for permiso in permisos.permisos)


def _puede_subir_tipo(
    tipo: Any,
    *,
    permisos: PermisosEfectivos,
    company_id: UUID | None,
    context: str,
) -> bool:
    if tipo.context != context:
        return False

    if company_id is None:
        interno = _tiene_codigo(permisos, Perm.DOCUMENTS_UPLOAD_INTERNAL)
        cliente = _tiene_codigo(permisos, Perm.DOCUMENTS_UPLOAD_CLIENT)
    else:
        interno = permisos.permite(Perm.DOCUMENTS_UPLOAD_INTERNAL, company_id=company_id)
        cliente = permisos.permite(Perm.DOCUMENTS_UPLOAD_CLIENT, company_id=company_id)

    if context == DocumentContext.DISPATCH:
        return interno
    if tipo.provided_by == ProvidedBy.STAFF:
        return interno
    if tipo.provided_by == ProvidedBy.CLIENT:
        # Un rol interno no usa el permiso de cliente para saltarse la regla
        # comercial del tipo, aunque por su matriz también posea ese permiso.
        return cliente and not interno
    return cliente or interno


async def _validar_tipo_para_subida(
    session: AsyncSession,
    *,
    document_type_id: UUID,
    issued_by: str,
    context: str,
    company_id: UUID,
    permisos: PermisosEfectivos,
) -> Any:
    tipo = await _tipo_de_documento(session, document_type_id)
    if not _puede_subir_tipo(tipo, permisos=permisos, company_id=company_id, context=context):
        # El tipo oculto e inexistente son indistinguibles para evitar revelar
        # catálogos internos a clientes.
        raise RecursoNoEncontrado("Tipo de documento no encontrado.")
    if issued_by not in set(tipo.issued_by_options):
        raise ReglaDeNegocioViolada(
            f"El emisor {issued_by} no es válido para {tipo.code}.",
            code="DOCUMENT_ISSUER_INVALID",
        )
    return tipo


def _storage_key(company_id: UUID, document_id: UUID, safe_name: str) -> str:
    """Ruta dentro del bucket.

    Incluye la empresa para que una política de bucket pueda aislarlas más
    adelante, y un sufijo aleatorio para que la clave no sea adivinable a
    partir del id: aunque la lectura pública está bloqueada, una clave
    predecible es una defensa menos.
    """
    hoy = datetime.now(UTC)
    sufijo = secrets.token_hex(8)
    return f"{company_id}/{hoy:%Y/%m}/{document_id}-{sufijo}-{safe_name}"


async def preparar_subida(
    session: AsyncSession,
    *,
    shipment_id: UUID,
    document_type_id: UUID,
    issued_by: str,
    original_name: str,
    company_id: UUID,
    actor_user_id: UUID,
    permisos: PermisosEfectivos,
) -> SubidaPreparada:
    """Primer tiempo: reserva el documento y emite la URL firmada.

    El documento queda en `UPLOADING`: existe en la base pero no es descargable
    ni satisface ningún requisito hasta que `completar()` lo verifique.
    """
    carga = (
        await session.execute(
            text("SELECT archived_at FROM shipments WHERE id = :s AND deleted_at IS NULL"),
            {"s": shipment_id},
        )
    ).one_or_none()
    if carga is None:
        raise RecursoNoEncontrado("Carga no encontrada.")
    if carga.archived_at is not None:
        raise CargaArchivada("Esta carga fue archivada y ya no admite documentos nuevos.")

    tipo = await _validar_tipo_para_subida(
        session,
        document_type_id=document_type_id,
        issued_by=issued_by,
        context=DocumentContext.SHIPMENT.value,
        company_id=company_id,
        permisos=permisos,
    )

    preparada = await _reservar_documento(
        session,
        tipo=tipo,
        issued_by=issued_by,
        original_name=original_name,
        company_id=company_id,
        actor_user_id=actor_user_id,
    )

    await session.execute(
        text("""
            INSERT INTO shipment_documents (shipment_id, document_id, document_type_id)
            VALUES (:s, :d, :t)
        """),
        {"s": shipment_id, "d": preparada.document_id, "t": document_type_id},
    )
    return preparada


async def _reservar_documento(
    session: AsyncSession,
    *,
    tipo: Any,
    issued_by: str,
    original_name: str,
    company_id: UUID,
    actor_user_id: UUID,
) -> SubidaPreparada:
    """Crea solo `documents`; el servicio padre agrega exactamente un enlace."""

    # Validación temprana de extensión: evita emitir una URL para algo que se
    # va a rechazar igual, y le da al usuario el error antes de subir 200 MB.
    extension = validar_extension(original_name)
    formato = extension.upper()
    if formato not in {f.upper() for f in tipo.allowed_formats}:
        metrics.upload_rechazado_total.labels(motivo="formato_no_permitido").inc()
        raise FormatoNoPermitido(
            f"El tipo de documento {tipo.code} no acepta archivos .{extension}. "
            f"Formatos aceptados: {', '.join(sorted(tipo.allowed_formats))}."
        )

    document_id = uuid4()
    safe_name = nombre_seguro(original_name)
    storage_key = _storage_key(company_id, document_id, safe_name)
    max_bytes = await _limite_para(session, formato)

    await session.execute(
        text("""
            INSERT INTO documents (
                id, company_id, uploaded_by, storage_provider, storage_key,
                original_name, safe_name, media_type, size_bytes, sha256,
                upload_status, issued_by
            )
            VALUES (
                :id, :company, :actor, 's3', :key,
                :original, :safe, 'application/octet-stream', 1, :hash_vacio,
                :upload_status, :issued_by
            )
        """),
        {
            "id": document_id,
            "company": company_id,
            "actor": actor_user_id,
            "key": storage_key,
            "original": original_name[:255],
            "safe": safe_name,
            # Placeholders hasta que `completar()` mida los bytes reales. Los
            # CHECK de la tabla exigen valores, y usar ceros los violaría.
            "hash_vacio": "0" * 64,
            "upload_status": UploadStatus.UPLOADING.value,
            "issued_by": issued_by,
        },
    )

    url = await s3.url_de_subida(storage_key)

    return SubidaPreparada(
        document_id=document_id,
        storage_key=storage_key,
        upload_url=url,
        expira_en=datetime.now(UTC).replace(microsecond=0),
        max_bytes=max_bytes,
    )


@dataclass(frozen=True)
class ProcesamientoPreparado:
    document_id: UUID
    upload_status: str


@dataclass(frozen=True)
class DocumentoCompletado:
    document_id: UUID
    media_type: str
    size_bytes: int
    sha256: str
    upload_status: str


async def _documento_del_recurso(
    session: AsyncSession,
    *,
    document_id: UUID,
    company_id: UUID,
    context: str,
    resource_id: UUID,
) -> Any:
    if context == DocumentContext.SHIPMENT:
        enlace = "shipment_documents"
        columna = "shipment_id"
    else:
        enlace = "dispatch_documents"
        columna = "dispatch_request_id"

    fila = (
        await session.execute(
            text(f"""
                SELECT d.id, d.storage_key, d.original_name, d.upload_status,
                       dt.allowed_formats, dt.code AS document_type_code
                FROM documents d
                JOIN {enlace} enlace ON enlace.document_id = d.id
                JOIN document_types dt ON dt.id = enlace.document_type_id
                WHERE d.id = :id AND d.company_id = :company
                  AND d.deleted_at IS NULL AND enlace.{columna} = :resource_id
                  AND dt.context = :context
                FOR UPDATE OF d
            """),  # noqa: S608
            {
                "id": document_id,
                "company": company_id,
                "resource_id": resource_id,
                "context": context,
            },
        )
    ).one_or_none()
    if fila is None:
        raise RecursoNoEncontrado("Documento no encontrado.")
    return fila


async def preparar_procesamiento(
    session: AsyncSession,
    *,
    document_id: UUID,
    company_id: UUID,
    context: str,
    resource_id: UUID,
) -> ProcesamientoPreparado:
    """Valida metadata y cabecera, marca PROCESSING y devuelve de inmediato."""
    fila = await _documento_del_recurso(
        session,
        document_id=document_id,
        company_id=company_id,
        context=context,
        resource_id=resource_id,
    )
    if fila.upload_status != UploadStatus.UPLOADING:
        raise Conflicto("Este documento ya fue procesado.", code="DOCUMENTO_YA_COMPLETADO")

    try:
        objeto = await s3.describir_objeto(fila.storage_key)
        cabecera = await s3.leer_rango(fila.storage_key, bytes_iniciales=_BYTES_CABECERA)
        resultado = validar_contenido(
            cabecera=cabecera,
            nombre=fila.original_name,
            formatos_permitidos=list(fila.allowed_formats),
        )
        validar_tamano(
            objeto.size_bytes,
            maximo=await _limite_para(session, resultado.formato),
        )
    except s3.ObjetoNoEncontrado as error:
        await _marcar_fallido(session, document_id)
        raise ArchivoInvalido(
            "No se encontró el archivo. Verifique que la subida haya terminado."
        ) from error
    except ReglaDeNegocioViolada as error:
        await _marcar_fallido(session, document_id)
        await s3.eliminar(fila.storage_key)
        metrics.upload_rechazado_total.labels(motivo=type(error).__name__).inc()
        raise

    await session.execute(
        text("""
            UPDATE documents
            SET media_type = :media_type, size_bytes = :size,
                safe_name = :safe_name, upload_status = 'PROCESSING'
            WHERE id = :id
        """),
        {
            "media_type": resultado.media_type,
            "size": objeto.size_bytes,
            "safe_name": resultado.safe_name,
            "id": document_id,
        },
    )
    return ProcesamientoPreparado(
        document_id=document_id, upload_status=UploadStatus.PROCESSING.value
    )


async def procesar_documento(session: AsyncSession, *, document_id: UUID) -> DocumentoCompletado:
    """Worker idempotente: calcula SHA-256 por chunks y confirma READY."""
    documento = (
        await session.execute(
            text("""
                SELECT id, storage_key, original_name, media_type, size_bytes,
                       upload_status
                FROM documents WHERE id = :id AND deleted_at IS NULL
                FOR UPDATE
            """),
            {"id": document_id},
        )
    ).one_or_none()
    if documento is None:
        raise RecursoNoEncontrado("Documento no encontrado.")
    if documento.upload_status == UploadStatus.READY:
        return DocumentoCompletado(
            document_id=document_id,
            media_type=documento.media_type,
            size_bytes=documento.size_bytes,
            sha256=(
                await session.execute(
                    text("SELECT sha256 FROM documents WHERE id = :id"),
                    {"id": document_id},
                )
            ).scalar_one(),
            upload_status=UploadStatus.READY.value,
        )
    if documento.upload_status != UploadStatus.PROCESSING:
        raise Conflicto(
            "El documento no está pendiente de procesamiento.",
            code="DOCUMENTO_NO_ESTA_PROCESANDO",
        )

    enlaces = list(
        (
            await session.execute(
                text("""
                    SELECT sd.shipment_id AS resource_id, 'SHIPMENT' AS context,
                           dt.code AS document_type_code
                    FROM shipment_documents sd
                    JOIN document_types dt ON dt.id = sd.document_type_id
                    WHERE sd.document_id = :id
                    UNION ALL
                    SELECT dd.dispatch_request_id, 'DISPATCH', dt.code
                    FROM dispatch_documents dd
                    JOIN document_types dt ON dt.id = dd.document_type_id
                    WHERE dd.document_id = :id
                """),
                {"id": document_id},
            )
        ).all()
    )
    if len(enlaces) != 1:
        raise Conflicto(
            "El documento no tiene un único recurso padre.",
            code="DOCUMENT_PARENT_INVALID",
        )
    enlace = enlaces[0]

    objeto = await s3.describir_objeto(documento.storage_key)
    digest = hashlib.sha256()
    total = 0
    async for chunk in s3.iterar_chunks(documento.storage_key):
        digest.update(chunk)
        total += len(chunk)
    if total != objeto.size_bytes or total != documento.size_bytes:
        await _marcar_fallido(session, document_id)
        raise ArchivoInvalido("El tamaño del archivo cambió durante el procesamiento.")

    sha256 = digest.hexdigest()
    await session.execute(
        text("""
            UPDATE documents SET sha256 = :sha256, upload_status = 'READY'
            WHERE id = :id AND upload_status = 'PROCESSING'
        """),
        {"sha256": sha256, "id": document_id},
    )
    if enlace.context == DocumentContext.SHIPMENT:
        await marcar_requisito_subido(session, document_id)

    from app.modules.audit.outbox import publicar

    await publicar(
        session,
        aggregate_type="document",
        aggregate_id=document_id,
        event_type=(
            "dispatch.document.ready"
            if enlace.context == DocumentContext.DISPATCH
            else "shipment.document.ready"
        ),
        payload={
            "resource_id": str(enlace.resource_id),
            "document_id": str(document_id),
            "document_type_code": enlace.document_type_code,
        },
        dedup_key=f"document:{document_id}:ready",
    )
    return DocumentoCompletado(
        document_id=document_id,
        media_type=documento.media_type,
        size_bytes=total,
        sha256=sha256,
        upload_status=UploadStatus.READY.value,
    )


async def completar(
    session: AsyncSession, *, document_id: UUID, company_id: UUID
) -> DocumentoCompletado:
    """Compatibilidad para consumidores internos que necesitan ejecución inmediata.

    La API pública no usa este camino: valida el recurso exacto, responde 202 y
    delega el hash al worker. Este adaptador conserva una sola implementación
    del procesamiento y también lee el objeto por bloques.
    """
    enlaces = list(
        (
            await session.execute(
                text("""
                    SELECT sd.shipment_id AS resource_id, 'SHIPMENT' AS context
                    FROM shipment_documents sd WHERE sd.document_id = :id
                    UNION ALL
                    SELECT dd.dispatch_request_id, 'DISPATCH'
                    FROM dispatch_documents dd WHERE dd.document_id = :id
                """),
                {"id": document_id},
            )
        ).all()
    )
    if len(enlaces) != 1:
        raise RecursoNoEncontrado("Documento no encontrado.")

    enlace = enlaces[0]
    await preparar_procesamiento(
        session,
        document_id=document_id,
        company_id=company_id,
        context=enlace.context,
        resource_id=enlace.resource_id,
    )
    return await procesar_documento(session, document_id=document_id)


async def marcar_requisito_subido(session: AsyncSession, document_id: UUID) -> None:
    """El requisito que este documento venía a satisfacer pasa a `UPLOADED`.

    NO pasa a `VERIFIED` (ADR-0003): subir un archivo no equivale a que
    Operaciones lo haya aceptado, así que el requisito sigue bloqueando hasta
    que alguien con `documents.verify` lo revise. El plan de trabajo decía
    `FULFILLED` automático; manda el ADR, que es la decisión más nueva.

    El enlace es por carga y tipo de documento, no por un `requirement_id` que
    el cliente mande: así no puede apuntar su factura al requisito que le
    convenga. Si hubiera varios abiertos del mismo tipo se toma el más viejo.
    """
    await session.execute(
        text("""
            UPDATE shipment_requirements
            SET status = :subido
            WHERE id = (
                SELECT r.id
                FROM shipment_requirements r
                JOIN shipment_documents sd
                  ON sd.shipment_id = r.shipment_id
                 AND sd.document_type_id = r.document_type_id
                WHERE sd.document_id = :documento
                  AND r.requirement_type = 'DOCUMENT'
                  AND r.status IN ('PENDING', 'REJECTED')
                ORDER BY r.created_at
                LIMIT 1
                FOR UPDATE OF r
            )
        """),
        {"subido": RequirementStatus.UPLOADED.value, "documento": document_id},
    )


_BYTES_CABECERA = 8192


async def _marcar_fallido(session: AsyncSession, document_id: UUID) -> None:
    await session.execute(
        text("UPDATE documents SET upload_status = :e WHERE id = :id"),
        {"e": UploadStatus.FAILED.value, "id": document_id},
    )


@dataclass(frozen=True)
class DescargaAutorizada:
    url: str
    nombre_archivo: str
    media_type: str


async def preparar_descarga(
    session: AsyncSession, *, document_id: UUID, company_ids: list[UUID] | None
) -> DescargaAutorizada:
    """Emite una URL firmada de corta duración.

    `company_ids is None` significa alcance global. El filtro va en el WHERE:
    traer la fila y descartarla después ya la habría expuesto al proceso.
    """
    condiciones = ["d.id = :id", "d.deleted_at IS NULL"]
    parametros: dict[str, object] = {"id": document_id}

    if company_ids is not None:
        condiciones.append("d.company_id = ANY(:empresas)")
        parametros["empresas"] = company_ids

    consulta = f"""
        SELECT d.storage_key, d.original_name, d.media_type, d.upload_status
        FROM documents d
        WHERE {" AND ".join(condiciones)}
    """  # noqa: S608

    fila = (await session.execute(text(consulta), parametros)).one_or_none()

    if fila is None:
        # Ajeno e inexistente son indistinguibles desde afuera.
        raise RecursoNoEncontrado("Documento no encontrado.")

    if fila.upload_status != UploadStatus.READY:
        raise DocumentoNoDisponible("El archivo todavía se está procesando.")

    url = await s3.url_de_descarga(fila.storage_key, nombre_archivo=fila.original_name)

    return DescargaAutorizada(
        url=url, nombre_archivo=fila.original_name, media_type=fila.media_type
    )


async def sembrar_limites(session: AsyncSession) -> None:
    """Deja los límites de ADR-0009 en `system_settings`. Idempotente."""
    for clave, bytes_ in LIMITES_POR_DEFECTO.items():
        await session.execute(
            text("""
                INSERT INTO system_settings (key, value)
                VALUES (:k, CAST(:v AS JSONB))
                ON CONFLICT (key) DO NOTHING
            """),
            {"k": clave, "v": json.dumps({"bytes": bytes_})},
        )


@dataclass(frozen=True)
class RequisitoDeCarga:
    id: UUID
    document_type_id: UUID | None
    code: str | None
    label: str
    description: str | None
    status: str
    required_from: str
    blocks_dispatch: bool
    allowed_formats: list[str]
    document_id: UUID | None


@dataclass(frozen=True)
class DocumentoDeCarga:
    id: UUID
    document_type_code: str
    document_type_label: str
    original_name: str
    media_type: str
    size_bytes: int
    upload_status: str
    created_at: datetime


@dataclass(frozen=True)
class ExpedienteDeCarga:
    requisitos: list[RequisitoDeCarga]
    documentos: list[DocumentoDeCarga]


async def renombrar(
    session: AsyncSession,
    *,
    document_id: UUID,
    nuevo_nombre: str,
    company_ids: list[UUID] | None,
) -> str:
    """Cambia el nombre visible de un documento. Devuelve el nombre aplicado.

    Solo el nombre que se muestra y con el que se descarga; la clave en el
    storage no se toca. Renombrar el objeto obligaría a copiarlo y borrarlo, con
    una ventana en la que el archivo no está en ningún lado.

    El nombre pasa por el mismo saneo que al subir: un nombre elegido a mano es
    tan capaz de traer una barra o un `..` como uno que viene del navegador.
    """
    limpio = (nuevo_nombre or "").strip()
    if not limpio:
        raise ReglaDeNegocioViolada("El nombre no puede quedar vacío.")

    seguro = nombre_seguro(limpio)

    condiciones = ["id = :id", "deleted_at IS NULL"]
    parametros: dict[str, Any] = {"id": document_id, "nombre": limpio[:255], "safe": seguro}
    if company_ids is not None:
        condiciones.append("company_id = ANY(:empresas)")
        parametros["empresas"] = company_ids

    fila = (
        await session.execute(
            text(f"""
                UPDATE documents SET original_name = :nombre, safe_name = :safe
                WHERE {" AND ".join(condiciones)}
                RETURNING original_name
            """),  # noqa: S608
            parametros,
        )
    ).scalar_one_or_none()

    if fila is None:
        # Ajeno e inexistente son indistinguibles desde afuera.
        raise RecursoNoEncontrado("Documento no encontrado.")

    nombre: str = fila
    return nombre


@dataclass(frozen=True)
class InvalidacionResultado:
    context: str
    resource_id: UUID


async def invalidar(
    session: AsyncSession,
    *,
    document_id: UUID,
    company_ids: list[UUID] | None,
    actor_user_id: UUID,
    motivo: str,
) -> InvalidacionResultado:
    """Saca un documento del expediente y conserva actor, motivo y evidencia.

    **No borra el objeto del storage.** Es la misma regla que para las cargas:
    un archivo que alguien subió y otro quitó puede ser evidencia de un error o
    de algo peor, y el archivo pesa mucho menos que la posibilidad de tener que
    reconstruir qué pasó.

    Si el documento satisfacía un requisito, ese requisito vuelve a quedar
    pendiente: dejarlo por cumplido con el archivo fuera haría que la carga
    pasara a despacho sin el papel que la habilita.
    """
    motivo_limpio = motivo.strip()
    if len(motivo_limpio) < 3:
        raise ReglaDeNegocioViolada(
            "Debe indicar el motivo de invalidación.",
            code="DOCUMENT_INVALIDATION_REASON_REQUIRED",
        )

    condiciones = ["id = :id", "deleted_at IS NULL"]
    parametros: dict[str, Any] = {
        "id": document_id,
        "actor": actor_user_id,
        "motivo": motivo_limpio,
    }
    if company_ids is not None:
        condiciones.append("company_id = ANY(:empresas)")
        parametros["empresas"] = company_ids

    fila = (
        await session.execute(
            text(f"""
                UPDATE documents
                SET deleted_at = now(), invalidated_by = :actor,
                    invalidation_reason = :motivo
                WHERE {" AND ".join(condiciones)}
                RETURNING id
            """),  # noqa: S608
            parametros,
        )
    ).scalar_one_or_none()

    if fila is None:
        raise RecursoNoEncontrado("Documento no encontrado.")

    enlaces = list(
        (
            await session.execute(
                text("""
                    SELECT shipment_id AS resource_id, document_type_id,
                           'SHIPMENT' AS context
                    FROM shipment_documents WHERE document_id = :d
                    UNION ALL
                    SELECT dispatch_request_id, document_type_id, 'DISPATCH'
                    FROM dispatch_documents WHERE document_id = :d
                """),
                {"d": document_id},
            )
        ).all()
    )
    if len(enlaces) != 1:
        raise Conflicto(
            "El documento no tiene un único recurso padre.",
            code="DOCUMENT_PARENT_INVALID",
        )
    enlace = enlaces[0]

    if enlace.context == DocumentContext.DISPATCH:
        return InvalidacionResultado(
            context=DocumentContext.DISPATCH.value,
            resource_id=enlace.resource_id,
        )

    # El requisito vuelve a pendiente solo si no queda ningún otro documento
    # vivo de ese tipo. Con dos facturas subidas, quitar una no deja a la carga
    # sin factura, y reabrirlo igual haría que Operaciones persiguiera un papel
    # que ya tiene.
    await session.execute(
        text("""
            UPDATE shipment_requirements r
            SET status = CASE WHEN EXISTS (
                    SELECT 1 FROM shipment_documents sd
                    JOIN documents d ON d.id = sd.document_id
                    WHERE sd.shipment_id = r.shipment_id
                      AND sd.document_type_id = r.document_type_id
                      AND d.deleted_at IS NULL
                      AND d.upload_status = 'READY'
                ) THEN :subido ELSE :pendiente END,
                verified_document_id = NULL,
                reviewed_document_id = NULL
            WHERE r.shipment_id = :carga
              AND r.document_type_id = :tipo
              AND r.requirement_type = 'DOCUMENT'
              AND (r.verified_document_id = :documento
                   OR r.reviewed_document_id = :documento
                   OR r.status IN ('UPLOADED', 'VERIFIED', 'REJECTED'))
        """),
        {
            "carga": enlace.resource_id,
            "tipo": enlace.document_type_id,
            "documento": document_id,
            "pendiente": RequirementStatus.PENDING.value,
            "subido": RequirementStatus.UPLOADED.value,
        },
    )
    return InvalidacionResultado(
        context=DocumentContext.SHIPMENT.value,
        resource_id=enlace.resource_id,
    )


async def expediente(
    session: AsyncSession, *, shipment_id: UUID, company_ids: list[UUID] | None
) -> ExpedienteDeCarga:
    """Qué documentos pide esta carga y cuáles ya tiene.

    El detalle de la carga solo devuelve contadores, y con un contador la
    interfaz puede decir "faltan documentos" pero no cuál, que es justo lo que
    la persona necesita para resolverlo.

    `company_ids is None` significa alcance global. El filtro va en el WHERE:
    traer la carga y descartarla después ya la habría expuesto al proceso.
    """
    condiciones = ["s.id = :shipment_id", "s.deleted_at IS NULL"]
    parametros: dict[str, Any] = {"shipment_id": shipment_id}

    if company_ids is not None:
        condiciones.append("s.company_id = ANY(:empresas)")
        parametros["empresas"] = company_ids

    donde = " AND ".join(condiciones)

    existe = (
        await session.execute(
            text(f"SELECT 1 FROM shipments s WHERE {donde}"),  # noqa: S608
            parametros,
        )
    ).scalar_one_or_none()

    if existe is None:
        raise RecursoNoEncontrado("Carga no encontrada.")

    requisitos = (
        await session.execute(
            text("""
                SELECT r.id, r.document_type_id, dt.code, r.title, r.description,
                       r.status, r.required_from, r.blocks_dispatch,
                       COALESCE(dt.allowed_formats, ARRAY[]::varchar[]) AS allowed_formats,
                       -- El documento que ya satisface (o intenta satisfacer)
                       -- este requisito, para poder descargarlo desde el mismo
                       -- renglón en vez de buscarlo en otra lista.
                       (SELECT sd.document_id
                          FROM shipment_documents sd
                          JOIN documents d ON d.id = sd.document_id
                         WHERE sd.shipment_id = r.shipment_id
                           AND sd.document_type_id = r.document_type_id
                           AND d.deleted_at IS NULL
                           -- `UPLOADING` es una reserva: el archivo puede no
                           -- haber llegado nunca. Devolverlo haría que la
                           -- interfaz ofrezca ver algo que no existe.
                           AND d.upload_status <> 'UPLOADING'
                         ORDER BY d.created_at DESC LIMIT 1) AS document_id
                FROM shipment_requirements r
                LEFT JOIN document_types dt ON dt.id = r.document_type_id
                WHERE r.shipment_id = :shipment_id
                  AND r.status NOT IN ('CANCELLED', 'NOT_APPLICABLE')
                ORDER BY r.blocks_dispatch DESC, r.created_at
            """),
            {"shipment_id": shipment_id},
        )
    ).all()

    documentos = (
        await session.execute(
            text("""
                SELECT d.id, dt.code, dt.label, d.original_name, d.media_type,
                       d.size_bytes, d.upload_status, d.created_at
                FROM shipment_documents sd
                JOIN documents d ON d.id = sd.document_id
                JOIN document_types dt ON dt.id = sd.document_type_id
                WHERE sd.shipment_id = :shipment_id AND d.deleted_at IS NULL
                ORDER BY d.created_at DESC
            """),
            {"shipment_id": shipment_id},
        )
    ).all()

    return ExpedienteDeCarga(
        requisitos=[
            RequisitoDeCarga(
                id=f.id,
                document_type_id=f.document_type_id,
                code=f.code,
                label=f.title,
                description=f.description,
                status=f.status,
                required_from=f.required_from,
                blocks_dispatch=f.blocks_dispatch,
                allowed_formats=list(f.allowed_formats),
                document_id=f.document_id,
            )
            for f in requisitos
        ],
        documentos=[
            DocumentoDeCarga(
                id=f.id,
                document_type_code=f.code,
                document_type_label=f.label,
                original_name=f.original_name,
                media_type=f.media_type,
                size_bytes=f.size_bytes,
                upload_status=f.upload_status,
                created_at=f.created_at,
            )
            for f in documentos
        ],
    )


async def tipos_de_documento(
    session: AsyncSession,
    *,
    context: str,
    permisos: PermisosEfectivos,
    company_id: UUID | None = None,
) -> list[Any]:
    """Catálogo filtrado con la misma matriz que vuelve a validar `presign`."""
    filas = list(
        (
            await session.execute(
                text("""
                    SELECT id, code, label, description, provided_by, context,
                           issued_by_options, allowed_formats, required_before_status
                    FROM document_types
                    WHERE is_active AND context = :context
                    ORDER BY label
                """),
                {"context": context},
            )
        ).all()
    )
    return [
        fila
        for fila in filas
        if _puede_subir_tipo(fila, permisos=permisos, company_id=company_id, context=context)
    ]


@dataclass(frozen=True)
class DocumentoDeDespacho:
    id: UUID
    document_type_code: str
    document_type_label: str
    original_name: str
    media_type: str
    size_bytes: int
    upload_status: str
    created_at: datetime


async def _despacho_visible(
    session: AsyncSession, *, dispatch_id: UUID, company_ids: list[UUID] | None
) -> UUID:
    """Devuelve la empresa del despacho, o 404 si el actor no lo puede ver.

    El filtro va en el WHERE: traer la fila y descartarla después ya la habría
    expuesto al proceso.
    """
    condiciones = ["d.id = :dispatch_id"]
    parametros: dict[str, Any] = {"dispatch_id": dispatch_id}

    if company_ids is not None:
        condiciones.append("d.company_id = ANY(:empresas)")
        parametros["empresas"] = company_ids

    empresa = (
        await session.execute(
            text(f"SELECT d.company_id FROM dispatch_requests d WHERE {' AND '.join(condiciones)}"),  # noqa: S608
            parametros,
        )
    ).scalar_one_or_none()

    if empresa is None:
        raise RecursoNoEncontrado("Solicitud de despacho no encontrada.")

    resultado: UUID = empresa
    return resultado


async def empresa_de_despacho_visible(
    session: AsyncSession, *, dispatch_id: UUID, company_ids: list[UUID] | None
) -> UUID:
    """API pública del servicio para validar alcance antes de completar."""
    return await _despacho_visible(session, dispatch_id=dispatch_id, company_ids=company_ids)


async def documentos_de_despacho(
    session: AsyncSession, *, dispatch_id: UUID, company_ids: list[UUID] | None
) -> list[DocumentoDeDespacho]:
    """El BL y las facturas que cuelgan de la solicitud, no de una carga suelta.

    En el sistema viejo eran dos pantallas distintas porque eran dos tablas
    distintas. Acá es una sola lista: para quien la mira, son los papeles del
    despacho.
    """
    await _despacho_visible(session, dispatch_id=dispatch_id, company_ids=company_ids)

    filas = (
        await session.execute(
            text("""
                SELECT d.id, dt.code, dt.label, d.original_name, d.media_type,
                       d.size_bytes, d.upload_status, d.created_at
                FROM dispatch_documents dd
                JOIN documents d ON d.id = dd.document_id
                JOIN document_types dt ON dt.id = dd.document_type_id
                WHERE dd.dispatch_request_id = :d AND d.deleted_at IS NULL
                ORDER BY dt.code, d.created_at DESC
            """),
            {"d": dispatch_id},
        )
    ).all()

    return [
        DocumentoDeDespacho(
            id=f.id,
            document_type_code=f.code,
            document_type_label=f.label,
            original_name=f.original_name,
            media_type=f.media_type,
            size_bytes=f.size_bytes,
            upload_status=f.upload_status,
            created_at=f.created_at,
        )
        for f in filas
    ]


async def preparar_subida_de_despacho(
    session: AsyncSession,
    *,
    dispatch_id: UUID,
    document_type_id: UUID,
    issued_by: str,
    original_name: str,
    company_ids: list[UUID] | None,
    actor_user_id: UUID,
    permisos: PermisosEfectivos,
) -> SubidaPreparada:
    """Igual que la subida de una carga, colgando del despacho.

    Reusa `preparar_subida` para no tener dos caminos que validen distinto: el
    formato, el tamaño y el escaneo tienen que comportarse igual vengan de donde
    vengan.
    """
    empresa = await _despacho_visible(session, dispatch_id=dispatch_id, company_ids=company_ids)

    tipo = await _validar_tipo_para_subida(
        session,
        document_type_id=document_type_id,
        issued_by=issued_by,
        context=DocumentContext.DISPATCH.value,
        company_id=empresa,
        permisos=permisos,
    )
    preparada = await _reservar_documento(
        session,
        tipo=tipo,
        issued_by=issued_by,
        original_name=original_name,
        company_id=empresa,
        actor_user_id=actor_user_id,
    )

    await session.execute(
        text("""
            INSERT INTO dispatch_documents (dispatch_request_id, document_id, document_type_id)
            VALUES (:d, :doc, :t) ON CONFLICT DO NOTHING
        """),
        {"d": dispatch_id, "doc": preparada.document_id, "t": document_type_id},
    )

    return preparada

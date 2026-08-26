"""Subida en dos tiempos y descarga autorizada (Paso 3.1).

El archivo nunca pasa por la aplicación: el cliente sube directo al storage con
una URL firmada. Subir 250 MB a través de FastAPI ocuparía un worker durante
toda la transferencia.

A cambio, el servidor no ve los bytes mientras suben — por eso `completar()`
verifica sobre lo ya almacenado y no sobre lo que el cliente declaró.
"""

import hashlib
import io
import json
import secrets
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflicto, RecursoNoEncontrado, ReglaDeNegocioViolada
from app.core.observability import metrics
from app.infrastructure.storage import s3
from app.modules.documents.models import UploadStatus
from app.modules.documents.validation import (
    ArchivoInvalido,
    FormatoNoPermitido,
    nombre_seguro,
    validar_contenido,
    validar_extension,
    validar_tamano,
)
from app.modules.shipments.models import RequirementStatus

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
    # La carga a la que quedó ligado el documento. Al subir desde un despacho
    # quien llama no la conoce, y la necesita para cerrar la subida.
    shipment_id: UUID
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
                SELECT id, code, allowed_formats
                FROM document_types WHERE id = :id AND is_active
            """),
            {"id": document_type_id},
        )
    ).one_or_none()

    if fila is None:
        raise RecursoNoEncontrado("Tipo de documento no encontrado.")

    return fila


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
    original_name: str,
    company_id: UUID,
    actor_user_id: UUID,
) -> SubidaPreparada:
    """Primer tiempo: reserva el documento y emite la URL firmada.

    El documento queda en `UPLOADING`: existe en la base pero no es descargable
    ni satisface ningún requisito hasta que `completar()` lo verifique.
    """
    tipo = await _tipo_de_documento(session, document_type_id)

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
                upload_status
            )
            VALUES (
                :id, :company, :actor, 's3', :key,
                :original, :safe, 'application/octet-stream', 1, :hash_vacio,
                :upload_status
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
        },
    )

    await session.execute(
        text("""
            INSERT INTO shipment_documents (shipment_id, document_id, document_type_id)
            VALUES (:s, :d, :t)
        """),
        {"s": shipment_id, "d": document_id, "t": document_type_id},
    )

    url = await s3.url_de_subida(storage_key)

    return SubidaPreparada(
        document_id=document_id,
        shipment_id=shipment_id,
        storage_key=storage_key,
        upload_url=url,
        expira_en=datetime.now(UTC).replace(microsecond=0),
        max_bytes=max_bytes,
    )


@dataclass(frozen=True)
class DocumentoCompletado:
    document_id: UUID
    media_type: str
    size_bytes: int
    sha256: str
    # El estado real con el que quedó. Lo devuelve el servicio y no lo escribe
    # el router: cuando el router lo tenía a mano, siguió respondiendo
    # "PROCESSING" después de que el pipeline dejara de usar ese estado, y la
    # API mintió sobre si el documento se podía descargar.
    upload_status: str


async def completar(
    session: AsyncSession, *, document_id: UUID, company_id: UUID
) -> DocumentoCompletado:
    """Segundo tiempo: verificar lo que realmente se subió.

    Todo se mide sobre los bytes almacenados. Lo que el cliente haya declarado
    en el primer tiempo no se usa para nada aquí.

    Si algo falla, el documento queda en `FAILED` y el objeto se borra: una
    subida rechazada no debe dejar basura en el bucket.
    """
    fila = (
        await session.execute(
            text("""
                SELECT d.id, d.storage_key, d.original_name, d.upload_status,
                       dt.allowed_formats
                FROM documents d
                JOIN shipment_documents sd ON sd.document_id = d.id
                JOIN document_types dt ON dt.id = sd.document_type_id
                WHERE d.id = :id AND d.company_id = :company AND d.deleted_at IS NULL
                FOR UPDATE OF d
            """),
            {"id": document_id, "company": company_id},
        )
    ).one_or_none()

    if fila is None:
        raise RecursoNoEncontrado("Documento no encontrado.")

    if fila.upload_status != UploadStatus.UPLOADING:
        raise Conflicto("Este documento ya fue procesado.", code="DOCUMENTO_YA_COMPLETADO")

    try:
        objeto = await s3.describir_objeto(fila.storage_key)
    except s3.ObjetoNoEncontrado as error:
        await _marcar_fallido(session, document_id)
        raise ArchivoInvalido(
            "No se encontró el archivo. Verificá que la subida haya terminado."
        ) from error

    try:
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
    except Exception as error:
        # Rechazado: se marca y se borra el objeto. Es el único caso en que un
        # archivo se elimina del storage — nunca uno ya válido (ADR-0007).
        await _marcar_fallido(session, document_id)
        await s3.eliminar(fila.storage_key)
        metrics.upload_rechazado_total.labels(motivo=type(error).__name__).inc()
        raise

    contenido = await s3.leer_completo(fila.storage_key)
    sha256 = hashlib.sha256(contenido).hexdigest()

    await session.execute(
        text("""
            UPDATE documents
            SET media_type = :media_type,
                size_bytes = :size,
                sha256 = :sha256,
                safe_name = :safe_name,
                upload_status = :estado
            WHERE id = :id
        """),
        {
            "media_type": resultado.media_type,
            "size": objeto.size_bytes,
            "sha256": sha256,
            "safe_name": resultado.safe_name,
            # READY: el documento queda descargable en cuanto termina la subida.
            # Antes pasaba por PROCESSING esperando al antivirus, que se retiró
            # por decisión de AMVARMAR (ver ADR-0009).
            "estado": UploadStatus.READY.value,
            "id": document_id,
        },
    )

    await _marcar_requisito_subido(session, document_id)

    return DocumentoCompletado(
        document_id=document_id,
        media_type=resultado.media_type,
        size_bytes=objeto.size_bytes,
        sha256=sha256,
        upload_status=UploadStatus.READY.value,
    )


async def _marcar_requisito_subido(session: AsyncSession, document_id: UUID) -> None:
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


async def invalidar(
    session: AsyncSession,
    *,
    document_id: UUID,
    company_ids: list[UUID] | None,
) -> UUID:
    """Saca un documento del expediente. Devuelve la carga a la que pertenecía.

    **No borra el objeto del storage.** Es la misma regla que para las cargas:
    un archivo que alguien subió y otro quitó puede ser evidencia de un error o
    de algo peor, y el archivo pesa mucho menos que la posibilidad de tener que
    reconstruir qué pasó.

    Si el documento satisfacía un requisito, ese requisito vuelve a quedar
    pendiente: dejarlo por cumplido con el archivo fuera haría que la carga
    pasara a despacho sin el papel que la habilita.
    """
    condiciones = ["id = :id", "deleted_at IS NULL"]
    parametros: dict[str, Any] = {"id": document_id}
    if company_ids is not None:
        condiciones.append("company_id = ANY(:empresas)")
        parametros["empresas"] = company_ids

    fila = (
        await session.execute(
            text(f"""
                UPDATE documents SET deleted_at = now()
                WHERE {" AND ".join(condiciones)}
                RETURNING id
            """),  # noqa: S608
            parametros,
        )
    ).scalar_one_or_none()

    if fila is None:
        raise RecursoNoEncontrado("Documento no encontrado.")

    enlace = (
        await session.execute(
            text("""
                SELECT shipment_id, document_type_id
                FROM shipment_documents WHERE document_id = :d
            """),
            {"d": document_id},
        )
    ).one_or_none()

    if enlace is None:
        # Documento de despacho, sin carga asociada.
        raise RecursoNoEncontrado("Documento no encontrado.")

    # El requisito vuelve a pendiente solo si no queda ningún otro documento
    # vivo de ese tipo. Con dos facturas subidas, quitar una no deja a la carga
    # sin factura, y reabrirlo igual haría que Operaciones persiguiera un papel
    # que ya tiene.
    await session.execute(
        text("""
            UPDATE shipment_requirements r
            SET status = :pendiente
            WHERE r.shipment_id = :carga
              AND r.document_type_id = :tipo
              AND r.requirement_type = 'DOCUMENT'
              AND r.status = :subido
              AND NOT EXISTS (
                  SELECT 1 FROM shipment_documents sd
                  JOIN documents d ON d.id = sd.document_id
                  WHERE sd.shipment_id = r.shipment_id
                    AND sd.document_type_id = r.document_type_id
                    AND d.deleted_at IS NULL
              )
        """),
        {
            "carga": enlace.shipment_id,
            "tipo": enlace.document_type_id,
            "pendiente": RequirementStatus.PENDING.value,
            "subido": RequirementStatus.UPLOADED.value,
        },
    )

    carga: UUID = enlace.shipment_id
    return carga


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


async def tipos_de_documento(session: AsyncSession) -> list[Any]:
    """Catálogo activo, para que la interfaz sepa qué se puede subir."""
    return list(
        (
            await session.execute(
                text("""
                    SELECT id, code, label, description, provided_by,
                           allowed_formats, required_before_status
                    FROM document_types WHERE is_active ORDER BY label
                """)
            )
        ).all()
    )


@dataclass(frozen=True)
class ArchivoParaZip:
    nombre: str
    contenido: bytes


async def paquete_de_documentos(
    session: AsyncSession, *, shipment_id: UUID, company_ids: list[UUID] | None
) -> tuple[str, bytes]:
    """Arma un ZIP con los documentos descargables de una carga.

    El sistema viejo tenía "descargar todos" y se usa cuando hay que mandarle el
    expediente completo a un agente aduanal: bajar ocho archivos uno por uno es
    trabajo que la máquina puede hacer.

    **ADR-0009 prohibió los ZIP en la SUBIDA**, no en la descarga. Son cosas
    distintas: uno que entra puede esconder cualquier cosa; uno que sale lo
    armamos nosotros.

    Solo entran los archivos cuya subida terminó. Si alguno quedó a medias se
    omite y su ausencia se anota dentro del propio ZIP: omitirlo en silencio
    haría creer que la carga no lo tenía.
    """
    condiciones = ["s.id = :shipment_id", "s.deleted_at IS NULL"]
    parametros: dict[str, Any] = {"shipment_id": shipment_id}

    if company_ids is not None:
        condiciones.append("s.company_id = ANY(:empresas)")
        parametros["empresas"] = company_ids

    carga = (
        await session.execute(
            text(f"SELECT s.shipment_number FROM shipments s WHERE {' AND '.join(condiciones)}"),  # noqa: S608
            parametros,
        )
    ).scalar_one_or_none()

    if carga is None:
        raise RecursoNoEncontrado("Carga no encontrada.")

    filas = (
        await session.execute(
            text("""
                SELECT d.id, d.storage_key, d.original_name, d.upload_status,
                       dt.code AS tipo
                FROM shipment_documents sd
                JOIN documents d ON d.id = sd.document_id
                JOIN document_types dt ON dt.id = sd.document_type_id
                WHERE sd.shipment_id = :s AND d.deleted_at IS NULL
                ORDER BY dt.code, d.created_at
            """),
            {"s": shipment_id},
        )
    ).all()

    if not filas:
        raise RecursoNoEncontrado("Esta carga no tiene documentos.")

    omitidos: list[str] = []
    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as paquete:
        usados: set[str] = set()

        for fila in filas:
            if fila.upload_status != UploadStatus.READY:
                omitidos.append(f"{fila.original_name}: {_motivo_omision(fila)}")
                continue

            contenido = await s3.leer_completo(fila.storage_key)
            # El mismo nombre dos veces dentro de un ZIP hace que uno pise al
            # otro al extraer.
            nombre = _nombre_unico(f"{fila.tipo}/{fila.original_name}", usados)
            paquete.writestr(nombre, contenido)

        if omitidos:
            paquete.writestr(
                "DOCUMENTOS-NO-INCLUIDOS.txt",
                "Estos documentos existen pero no se pudieron incluir:\n\n"
                + "\n".join(f"- {linea}" for linea in omitidos)
                + "\n\nConsulte el expediente en el sistema para ver su estado.\n",
            )

    return f"{carga}-documentos.zip", buffer.getvalue()


def _motivo_omision(fila: Any) -> str:
    if fila.upload_status != UploadStatus.READY:
        return "la subida no se completó"
    return "no está disponible"


def _nombre_unico(nombre: str, usados: set[str]) -> str:
    if nombre not in usados:
        usados.add(nombre)
        return nombre

    raiz, punto, extension = nombre.rpartition(".")
    base = raiz if punto else nombre
    sufijo = extension if punto else ""

    contador = 2
    while True:
        candidato = f"{base}-{contador}{'.' + sufijo if sufijo else ''}"
        if candidato not in usados:
            usados.add(candidato)
            return candidato
        contador += 1


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
    original_name: str,
    company_ids: list[UUID] | None,
    actor_user_id: UUID,
) -> SubidaPreparada:
    """Igual que la subida de una carga, colgando del despacho.

    Reusa `preparar_subida` para no tener dos caminos que validen distinto: el
    formato, el tamaño y el escaneo tienen que comportarse igual vengan de donde
    vengan.
    """
    empresa = await _despacho_visible(session, dispatch_id=dispatch_id, company_ids=company_ids)

    # Se apoya en la primera carga del despacho para reutilizar el flujo de
    # subida, y después se agrega el enlace con la solicitud.
    carga = (
        await session.execute(
            text("""
                SELECT shipment_id FROM dispatch_request_shipments
                WHERE dispatch_request_id = :d
                ORDER BY added_at LIMIT 1
            """),
            {"d": dispatch_id},
        )
    ).scalar_one_or_none()

    if carga is None:
        raise Conflicto(
            "La solicitud no tiene cargas asociadas, así que no se le puede adjuntar un documento.",
            code="DESPACHO_SIN_CARGAS",
        )

    preparada = await preparar_subida(
        session,
        shipment_id=carga,
        document_type_id=document_type_id,
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


async def paquete_de_bls(
    session: AsyncSession, *, dispatch_id: UUID, company_ids: list[UUID] | None
) -> tuple[str, bytes]:
    """Los Bills of Lading de un despacho en un ZIP.

    Existía en el sistema viejo: un despacho puede llevar varios BL y el cliente
    los necesita todos juntos para su agente.
    """
    await _despacho_visible(session, dispatch_id=dispatch_id, company_ids=company_ids)

    numero = (
        await session.execute(
            text("SELECT dispatch_number FROM dispatch_requests WHERE id = :d"),
            {"d": dispatch_id},
        )
    ).scalar_one()

    filas = (
        await session.execute(
            text("""
                SELECT d.storage_key, d.original_name, d.upload_status
                FROM dispatch_documents dd
                JOIN documents d ON d.id = dd.document_id
                JOIN document_types dt ON dt.id = dd.document_type_id
                WHERE dd.dispatch_request_id = :d AND dt.code = 'BL'
                  AND d.deleted_at IS NULL
                ORDER BY d.created_at
            """),
            {"d": dispatch_id},
        )
    ).all()

    if not filas:
        raise RecursoNoEncontrado("Esta solicitud todavía no tiene Bills of Lading.")

    buffer = io.BytesIO()
    omitidos: list[str] = []
    usados: set[str] = set()

    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as paquete:
        for fila in filas:
            if fila.upload_status != UploadStatus.READY:
                omitidos.append(f"{fila.original_name}: {_motivo_omision(fila)}")
                continue
            contenido = await s3.leer_completo(fila.storage_key)
            paquete.writestr(_nombre_unico(fila.original_name, usados), contenido)

        if omitidos:
            paquete.writestr(
                "DOCUMENTOS-NO-INCLUIDOS.txt",
                "Estos Bills of Lading existen pero no se pudieron incluir:\n\n"
                + "\n".join(f"- {linea}" for linea in omitidos)
                + "\n",
            )

    return f"{numero}-bls.zip", buffer.getvalue()

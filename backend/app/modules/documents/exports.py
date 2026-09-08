"""Exportaciones ZIP asincrónicas con memoria acotada."""

import asyncio
import hashlib
import json
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import Conflicto, RecursoNoEncontrado, ReglaDeNegocioViolada
from app.infrastructure.storage import s3
from app.modules.documents.models import (
    DocumentContext,
    ExportKind,
    ExportStatus,
    UploadStatus,
)


class ExportacionNoDisponible(Conflicto):
    code = "DOCUMENT_EXPORT_NOT_READY"


class FuenteDeExportacionCambio(Conflicto):
    code = "DOCUMENT_EXPORT_SOURCE_CHANGED"


@dataclass(frozen=True)
class ArchivoFuente:
    id: UUID
    storage_key: str
    original_name: str
    safe_name: str
    upload_status: str
    sha256: str
    tipo: str


@dataclass(frozen=True)
class RecursoExportable:
    company_id: UUID
    referencia: str
    archivos: list[ArchivoFuente]


@dataclass(frozen=True)
class Exportacion:
    id: UUID
    resource_type: str
    resource_id: UUID
    kind: str
    status: str
    size_bytes: int | None
    sha256: str | None
    error_code: str | None
    expires_at: datetime
    created_at: datetime


@dataclass(frozen=True)
class DescargaExportacion:
    url: str
    filename: str


@dataclass(frozen=True)
class PlanExportacion:
    job_id: UUID
    company_id: UUID
    context: str
    resource_id: UUID
    kind: str
    result_name: str
    storage_key: str
    archivos: list[ArchivoFuente]


def _a_archivos(filas: list[Any]) -> list[ArchivoFuente]:
    return [
        ArchivoFuente(
            id=f.id,
            storage_key=f.storage_key,
            original_name=f.original_name,
            safe_name=f.safe_name,
            upload_status=f.upload_status,
            sha256=f.sha256,
            tipo=f.tipo,
        )
        for f in filas
    ]


async def _recurso_exportable(
    session: AsyncSession,
    *,
    context: str,
    resource_id: UUID,
    kind: str,
    company_ids: list[UUID] | None,
) -> RecursoExportable:
    if context == DocumentContext.SHIPMENT:
        if kind != ExportKind.ALL_DOCUMENTS:
            raise ReglaDeNegocioViolada("Las cargas solo admiten ALL_DOCUMENTS.")
        condiciones = ["s.id = :id", "s.deleted_at IS NULL"]
        if company_ids is not None:
            condiciones.append("s.company_id = ANY(:empresas)")
        recurso = (
            await session.execute(
                text(f"""
                    SELECT s.company_id, s.shipment_number AS referencia
                    FROM shipments s WHERE {" AND ".join(condiciones)}
                """),  # noqa: S608
                {"id": resource_id, "empresas": company_ids},
            )
        ).one_or_none()
        if recurso is None:
            raise RecursoNoEncontrado("Carga no encontrada.")
        filas = list(
            (
                await session.execute(
                    text("""
                        SELECT d.id, d.storage_key, d.original_name, d.safe_name,
                               d.upload_status, d.sha256, dt.code AS tipo
                        FROM shipment_documents sd
                        JOIN documents d ON d.id = sd.document_id
                        JOIN document_types dt ON dt.id = sd.document_type_id
                        WHERE sd.shipment_id = :id AND d.deleted_at IS NULL
                        ORDER BY dt.code, d.created_at, d.id
                    """),
                    {"id": resource_id},
                )
            ).all()
        )
    elif context == DocumentContext.DISPATCH:
        if kind not in {ExportKind.ALL_DOCUMENTS, ExportKind.BLS}:
            raise ReglaDeNegocioViolada("Tipo de exportación no válido.")
        condiciones = ["dr.id = :id"]
        if company_ids is not None:
            condiciones.append("dr.company_id = ANY(:empresas)")
        recurso = (
            await session.execute(
                text(f"""
                    SELECT dr.company_id, dr.dispatch_number AS referencia
                    FROM dispatch_requests dr WHERE {" AND ".join(condiciones)}
                """),  # noqa: S608
                {"id": resource_id, "empresas": company_ids},
            )
        ).one_or_none()
        if recurso is None:
            raise RecursoNoEncontrado("Solicitud de despacho no encontrada.")
        filtro = "AND upper(dt.code) = 'BL'" if kind == ExportKind.BLS else ""
        filas = list(
            (
                await session.execute(
                    text(f"""
                        SELECT d.id, d.storage_key, d.original_name, d.safe_name,
                               d.upload_status, d.sha256, dt.code AS tipo
                        FROM dispatch_documents dd
                        JOIN documents d ON d.id = dd.document_id
                        JOIN document_types dt ON dt.id = dd.document_type_id
                        WHERE dd.dispatch_request_id = :id AND d.deleted_at IS NULL
                        {filtro}
                        ORDER BY dt.code, d.created_at, d.id
                    """),  # noqa: S608
                    {"id": resource_id},
                )
            ).all()
        )
    else:
        raise ReglaDeNegocioViolada("Contexto de exportación no válido.")

    if not filas:
        raise RecursoNoEncontrado("El recurso no tiene documentos para exportar.")
    return RecursoExportable(
        company_id=recurso.company_id,
        referencia=recurso.referencia,
        archivos=_a_archivos(filas),
    )


def _fingerprint(archivos: list[ArchivoFuente]) -> str:
    serializado = json.dumps(
        [
            {
                "id": str(f.id),
                "status": f.upload_status,
                "sha256": f.sha256,
                "tipo": f.tipo,
                "nombre": f.safe_name,
            }
            for f in archivos
        ],
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serializado.encode()).hexdigest()


def _desde_fila(fila: Any) -> Exportacion:
    return Exportacion(
        id=fila.id,
        resource_type=fila.resource_type,
        resource_id=fila.resource_id,
        kind=fila.kind,
        status=fila.status,
        size_bytes=fila.size_bytes,
        sha256=fila.sha256,
        error_code=fila.error_code,
        expires_at=fila.expires_at,
        created_at=fila.created_at,
    )


async def crear(
    session: AsyncSession,
    *,
    requested_by: UUID,
    context: str,
    resource_id: UUID,
    kind: str,
    company_ids: list[UUID] | None,
) -> Exportacion:
    recurso = await _recurso_exportable(
        session,
        context=context,
        resource_id=resource_id,
        kind=kind,
        company_ids=company_ids,
    )
    fingerprint = _fingerprint(recurso.archivos)
    expira = datetime.now(UTC) + timedelta(hours=get_settings().document_export_ttl_hours)
    fila = (
        await session.execute(
            text("""
                INSERT INTO document_export_jobs
                    (requested_by, company_id, resource_type, resource_id, kind,
                     source_fingerprint, status, expires_at)
                VALUES (:usuario, :empresa, :contexto, :recurso, :tipo,
                        :fingerprint, 'PENDING', :expira)
                ON CONFLICT ON CONSTRAINT uq_document_export_jobs_solicitud_fuente
                DO UPDATE SET
                    status = CASE
                        WHEN document_export_jobs.status IN ('FAILED', 'EXPIRED')
                          OR document_export_jobs.expires_at <= now()
                        THEN 'PENDING' ELSE document_export_jobs.status END,
                    storage_key = CASE
                        WHEN document_export_jobs.status IN ('FAILED', 'EXPIRED')
                          OR document_export_jobs.expires_at <= now()
                        THEN NULL ELSE document_export_jobs.storage_key END,
                    result_name = CASE
                        WHEN document_export_jobs.status IN ('FAILED', 'EXPIRED')
                          OR document_export_jobs.expires_at <= now()
                        THEN NULL ELSE document_export_jobs.result_name END,
                    size_bytes = CASE
                        WHEN document_export_jobs.status IN ('FAILED', 'EXPIRED')
                          OR document_export_jobs.expires_at <= now()
                        THEN NULL ELSE document_export_jobs.size_bytes END,
                    sha256 = CASE
                        WHEN document_export_jobs.status IN ('FAILED', 'EXPIRED')
                          OR document_export_jobs.expires_at <= now()
                        THEN NULL ELSE document_export_jobs.sha256 END,
                    error_code = NULL,
                    expires_at = CASE
                        WHEN document_export_jobs.status IN ('FAILED', 'EXPIRED')
                          OR document_export_jobs.expires_at <= now()
                        THEN EXCLUDED.expires_at ELSE document_export_jobs.expires_at END,
                    started_at = CASE
                        WHEN document_export_jobs.status IN ('FAILED', 'EXPIRED')
                          OR document_export_jobs.expires_at <= now()
                        THEN NULL ELSE document_export_jobs.started_at END,
                    completed_at = CASE
                        WHEN document_export_jobs.status IN ('FAILED', 'EXPIRED')
                          OR document_export_jobs.expires_at <= now()
                        THEN NULL ELSE document_export_jobs.completed_at END,
                    updated_at = now()
                RETURNING id, resource_type, resource_id, kind, status,
                          size_bytes, sha256, error_code, expires_at, created_at
            """),
            {
                "usuario": requested_by,
                "empresa": recurso.company_id,
                "contexto": context,
                "recurso": resource_id,
                "tipo": kind,
                "fingerprint": fingerprint,
                "expira": expira,
            },
        )
    ).one()
    return _desde_fila(fila)


def _condiciones_scope(
    *, actor_user_id: UUID, company_ids: list[UUID] | None
) -> tuple[list[str], dict[str, object]]:
    condiciones = ["id = :id"]
    parametros: dict[str, object] = {"actor": actor_user_id}
    if company_ids is not None:
        condiciones.append("(requested_by = :actor OR company_id = ANY(:empresas))")
        parametros["empresas"] = company_ids
    return condiciones, parametros


async def obtener(
    session: AsyncSession,
    *,
    job_id: UUID,
    actor_user_id: UUID,
    company_ids: list[UUID] | None,
) -> Exportacion:
    condiciones, parametros = _condiciones_scope(
        actor_user_id=actor_user_id, company_ids=company_ids
    )
    parametros["id"] = job_id
    fila = (
        await session.execute(
            text(f"""
                SELECT id, resource_type, resource_id, kind,
                       CASE WHEN expires_at <= now() AND status = 'READY'
                            THEN 'EXPIRED' ELSE status END AS status,
                       size_bytes, sha256, error_code, expires_at, created_at
                FROM document_export_jobs
                WHERE {" AND ".join(condiciones)}
            """),  # noqa: S608
            parametros,
        )
    ).one_or_none()
    if fila is None:
        raise RecursoNoEncontrado("Exportación no encontrada.")
    return _desde_fila(fila)


async def preparar_descarga(
    session: AsyncSession,
    *,
    job_id: UUID,
    actor_user_id: UUID,
    company_ids: list[UUID] | None,
) -> DescargaExportacion:
    condiciones, parametros = _condiciones_scope(
        actor_user_id=actor_user_id, company_ids=company_ids
    )
    parametros["id"] = job_id
    fila = (
        await session.execute(
            text(f"""
                SELECT status, storage_key, result_name, expires_at
                FROM document_export_jobs
                WHERE {" AND ".join(condiciones)}
            """),  # noqa: S608
            parametros,
        )
    ).one_or_none()
    if fila is None:
        raise RecursoNoEncontrado("Exportación no encontrada.")
    if fila.expires_at <= datetime.now(UTC):
        raise ExportacionNoDisponible(
            "La exportación venció; solicite una nueva.",
            code="DOCUMENT_EXPORT_EXPIRED",
        )
    if fila.status != ExportStatus.READY or not fila.storage_key or not fila.result_name:
        raise ExportacionNoDisponible("La exportación todavía no está lista.")
    return DescargaExportacion(
        url=await s3.url_de_descarga(fila.storage_key, nombre_archivo=fila.result_name),
        filename=fila.result_name,
    )


async def _reclamar(session: AsyncSession, job_id: UUID) -> PlanExportacion | None:
    fila = (
        await session.execute(
            text("""
                UPDATE document_export_jobs
                SET status = 'PROCESSING', started_at = now(), updated_at = now(),
                    error_code = NULL
                WHERE id = :id AND status = 'PENDING' AND expires_at > now()
                RETURNING id, company_id, resource_type, resource_id, kind,
                          source_fingerprint
            """),
            {"id": job_id},
        )
    ).one_or_none()
    if fila is None:
        return None
    recurso = await _recurso_exportable(
        session,
        context=fila.resource_type,
        resource_id=fila.resource_id,
        kind=fila.kind,
        company_ids=None,
    )
    if _fingerprint(recurso.archivos) != fila.source_fingerprint:
        raise FuenteDeExportacionCambio(
            "Los documentos cambiaron mientras se preparaba la exportación."
        )
    sufijo = "bls" if fila.kind == ExportKind.BLS else "documentos"
    return PlanExportacion(
        job_id=fila.id,
        company_id=fila.company_id,
        context=fila.resource_type,
        resource_id=fila.resource_id,
        kind=fila.kind,
        result_name=f"{recurso.referencia}-{sufijo}.zip",
        storage_key=f"exports/{fila.company_id}/{fila.id}.zip",
        archivos=recurso.archivos,
    )


def _nombre_unico(nombre: str, usados: set[str]) -> str:
    if nombre not in usados:
        usados.add(nombre)
        return nombre
    raiz, punto, extension = nombre.rpartition(".")
    base = raiz if punto else nombre
    sufijo = f".{extension}" if punto else ""
    contador = 2
    while f"{base}-{contador}{sufijo}" in usados:
        contador += 1
    resultado = f"{base}-{contador}{sufijo}"
    usados.add(resultado)
    return resultado


async def _construir_zip(plan: PlanExportacion, destino: Path) -> None:
    omitidos: list[str] = []
    usados: set[str] = set()
    with zipfile.ZipFile(
        destino,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        allowZip64=True,
        strict_timestamps=False,
    ) as paquete:
        for archivo in plan.archivos:
            if archivo.upload_status != UploadStatus.READY:
                omitidos.append(f"{archivo.original_name}: la subida no se completó")
                continue
            nombre_base = archivo.safe_name
            if plan.kind != ExportKind.BLS:
                nombre_base = f"{archivo.tipo}/{nombre_base}"
            nombre = _nombre_unico(nombre_base, usados)
            with paquete.open(nombre, "w", force_zip64=True) as salida:
                async for chunk in s3.iterar_chunks(archivo.storage_key):
                    salida.write(chunk)

        if omitidos:
            paquete.writestr(
                "DOCUMENTOS-NO-INCLUIDOS.txt",
                "Estos documentos no se incluyeron:\n\n"
                + "\n".join(f"- {linea}" for linea in omitidos)
                + "\n",
            )


def _hash_y_tamano(ruta: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    with ruta.open("rb") as archivo:
        while chunk := archivo.read(8 * 1024 * 1024):
            digest.update(chunk)
            total += len(chunk)
    return digest.hexdigest(), total


async def procesar(session: AsyncSession, *, job_id: UUID) -> Exportacion | None:
    """Reclama, genera y publica el ZIP. Es idempotente ante tareas duplicadas."""
    try:
        plan = await _reclamar(session, job_id)
        await session.commit()
        if plan is None:
            return None

        settings = get_settings()
        with tempfile.TemporaryDirectory(
            prefix="amvarmar-export-", dir=settings.document_export_temp_dir
        ) as directorio:
            ruta = Path(directorio) / plan.result_name
            await _construir_zip(plan, ruta)
            sha256, size_bytes = await asyncio.to_thread(_hash_y_tamano, ruta)
            await s3.subir_archivo(str(ruta), plan.storage_key, media_type="application/zip")

        fila = (
            await session.execute(
                text("""
                    UPDATE document_export_jobs
                    SET status = 'READY', storage_key = :key,
                        result_name = :nombre, size_bytes = :size,
                        sha256 = :sha, completed_at = now(), updated_at = now(),
                        error_code = NULL
                    WHERE id = :id AND status = 'PROCESSING'
                    RETURNING id, resource_type, resource_id, kind, status,
                              size_bytes, sha256, error_code, expires_at, created_at
                """),
                {
                    "id": job_id,
                    "key": plan.storage_key,
                    "nombre": plan.result_name,
                    "size": size_bytes,
                    "sha": sha256,
                },
            )
        ).one()
        await session.commit()
        return _desde_fila(fila)
    except Exception as error:
        await session.rollback()
        codigo = (
            FuenteDeExportacionCambio.code
            if isinstance(error, FuenteDeExportacionCambio)
            else "EXPORT_GENERATION_FAILED"
        )
        await session.execute(
            text("""
                UPDATE document_export_jobs
                SET status = 'FAILED', error_code = :codigo,
                    updated_at = now()
                WHERE id = :id AND status IN ('PENDING', 'PROCESSING')
            """),
            {"id": job_id, "codigo": codigo},
        )
        await session.commit()
        raise


async def reprogramar(session: AsyncSession, *, job_id: UUID) -> bool:
    resultado = await session.execute(
        text("""
            UPDATE document_export_jobs
            SET status = 'PENDING', error_code = NULL, started_at = NULL,
                updated_at = now()
            WHERE id = :id AND status = 'FAILED' AND expires_at > now()
            RETURNING id
        """),
        {"id": job_id},
    )
    await session.commit()
    return resultado.one_or_none() is not None


async def expirar(session: AsyncSession, *, limite: int = 100) -> int:
    filas = list(
        (
            await session.execute(
                text("""
                    SELECT id, storage_key FROM document_export_jobs
                    WHERE status = 'READY' AND expires_at <= now()
                    ORDER BY expires_at
                    LIMIT :limite
                    FOR UPDATE SKIP LOCKED
                """),
                {"limite": limite},
            )
        ).all()
    )
    for fila in filas:
        if fila.storage_key:
            await s3.eliminar(fila.storage_key)
        await session.execute(
            text("""
                UPDATE document_export_jobs
                SET status = 'EXPIRED', storage_key = NULL, updated_at = now()
                WHERE id = :id
            """),
            {"id": fila.id},
        )
    return len(filas)

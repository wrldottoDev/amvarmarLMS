"""Escaneo antivirus de documentos (Paso 3.2).

Regla que define el paso: **fail closed**. Un documento solo pasa a disponible
si el escáner dijo explícitamente que está limpio. Si el escáner está caído,
tarda demasiado o responde algo raro, el documento se queda pendiente y se
reintenta. Nunca se asume que algo no verificado es seguro.

Los archivos infectados NO se borran: quedan en cuarentena con el nombre de la
amenaza. Borrarlos automáticamente eliminaría la evidencia de un incidente.
"""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_engine, get_sessionmaker
from app.core.logging import obtener_logger
from app.core.observability import metrics
from app.infrastructure.antivirus import clamav
from app.infrastructure.storage import s3
from app.modules.audit.models import Outcome
from app.modules.audit.service import registrar
from app.modules.documents.models import ScanStatus, UploadStatus
from app.workers.app import celery_app

_log = obtener_logger("antivirus")

# Cuántos documentos toma el worker por pasada.
LOTE = 20


@dataclass(frozen=True)
class ResultadoLote:
    limpios: int
    infectados: int
    pendientes: int


async def escanear_documento(session: AsyncSession, document_id: UUID) -> str:
    """Escanea un documento y actualiza su estado. Devuelve el `scan_status`.

    Toda la actualización va en la transacción del llamador: si el commit falla,
    el documento queda pendiente y se reintenta, en vez de quedar marcado como
    limpio sin que el cambio se haya persistido.
    """
    fila = (
        await session.execute(
            text("""
                SELECT id, company_id, storage_key, original_name
                FROM documents
                WHERE id = :id AND scan_status = :pendiente AND deleted_at IS NULL
                FOR UPDATE SKIP LOCKED
            """),
            {"id": document_id, "pendiente": ScanStatus.PENDING.value},
        )
    ).one_or_none()

    if fila is None:
        # Ya lo tomó otro worker, o ya se escaneó.
        return ScanStatus.PENDING.value

    try:
        contenido = await s3.leer_completo(fila.storage_key)
    except Exception:
        _log.warning("documento_ilegible", document_id=str(document_id))
        # No se pudo leer: se deja pendiente para reintentar. Puede ser un fallo
        # transitorio del storage.
        return ScanStatus.PENDING.value

    try:
        veredicto = await clamav.escanear(contenido)
    except clamav.EscanerNoDisponible as error:
        # FAIL CLOSED. El documento sigue en PENDING y no es descargable.
        _log.warning("escaner_no_disponible", document_id=str(document_id), motivo=str(error))
        return ScanStatus.PENDING.value

    if veredicto.resultado is clamav.ResultadoEscaneo.INFECTADO:
        await _marcar(
            session,
            document_id,
            scan_status=ScanStatus.INFECTED,
            # El archivo queda en storage: es evidencia del incidente.
            upload_status=UploadStatus.FAILED,
        )
        await registrar(
            session,
            action="document.scan.infected",
            resource_type="document",
            resource_id=document_id,
            company_id=fila.company_id,
            outcome=Outcome.DENIED,
            reason=f"Amenaza detectada: {veredicto.amenaza}",
            after_data={"amenaza": veredicto.amenaza, "archivo": fila.original_name},
        )
        metrics.documento_infectado_total.inc()
        _log.warning(
            "documento_infectado",
            document_id=str(document_id),
            amenaza=veredicto.amenaza,
        )
        return ScanStatus.INFECTED.value

    await _marcar(
        session,
        document_id,
        scan_status=ScanStatus.CLEAN,
        # Recién ahora es descargable.
        upload_status=UploadStatus.READY,
    )
    await registrar(
        session,
        action="document.scan.clean",
        resource_type="document",
        resource_id=document_id,
        company_id=fila.company_id,
        outcome=Outcome.SUCCESS,
    )
    return ScanStatus.CLEAN.value


async def _marcar(
    session: AsyncSession,
    document_id: UUID,
    *,
    scan_status: ScanStatus,
    upload_status: UploadStatus,
) -> None:
    await session.execute(
        text("""
            UPDATE documents
            SET scan_status = :scan, upload_status = :upload, scanned_at = :ahora
            WHERE id = :id
        """),
        {
            "scan": scan_status.value,
            "upload": upload_status.value,
            "ahora": datetime.now(UTC),
            "id": document_id,
        },
    )


async def escanear_pendientes(session: AsyncSession, *, limite: int = LOTE) -> ResultadoLote:
    """Toma un lote de documentos pendientes y los escanea.

    `SKIP LOCKED` en la selección permite correr varios workers sin que se
    pisen: cada uno toma documentos distintos.
    """
    ids = list(
        (
            await session.execute(
                text("""
                    SELECT id FROM documents
                    WHERE scan_status = :pendiente
                      AND upload_status = :procesando
                      AND deleted_at IS NULL
                    ORDER BY created_at
                    LIMIT :limite
                    FOR UPDATE SKIP LOCKED
                """),
                {
                    "pendiente": ScanStatus.PENDING.value,
                    "procesando": UploadStatus.PROCESSING.value,
                    "limite": limite,
                },
            )
        )
        .scalars()
        .all()
    )

    conteos = {ScanStatus.CLEAN.value: 0, ScanStatus.INFECTED.value: 0, ScanStatus.PENDING.value: 0}
    for document_id in ids:
        resultado = await escanear_documento(session, document_id)
        conteos[resultado] = conteos.get(resultado, 0) + 1

    return ResultadoLote(
        limpios=conteos[ScanStatus.CLEAN.value],
        infectados=conteos[ScanStatus.INFECTED.value],
        pendientes=conteos[ScanStatus.PENDING.value],
    )


async def _ejecutar_lote() -> ResultadoLote:
    async with get_sessionmaker()() as session:
        resultado = await escanear_pendientes(session)
        await session.commit()
    return resultado


@celery_app.task(name="documentos.escanear_pendientes")
def tarea_escanear_pendientes() -> dict[str, int]:
    """Punto de entrada de Celery.

    Fina a propósito: la lógica vive en funciones async que los tests invocan
    directamente, sin necesidad de un broker corriendo.
    """
    resultado = asyncio.run(_ejecutar_lote())
    asyncio.run(get_engine().dispose())

    return {
        "limpios": resultado.limpios,
        "infectados": resultado.infectados,
        "pendientes": resultado.pendientes,
    }

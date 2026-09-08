"""Workers de generación y expiración de exportaciones documentales."""

import asyncio
from uuid import UUID

from app.core.database import get_engine, get_sessionmaker
from app.modules.documents import exports
from app.workers.app import celery_app


async def _procesar(job_id: UUID) -> dict[str, object]:
    async with get_sessionmaker()() as session:
        resultado = await exports.procesar(session, job_id=job_id)
    return {
        "job_id": str(job_id),
        "status": resultado.status if resultado else "UNCHANGED",
    }


async def _reprogramar(job_id: UUID) -> bool:
    async with get_sessionmaker()() as session:
        return await exports.reprogramar(session, job_id=job_id)


@celery_app.task(bind=True, name="documents.export", max_retries=3)
def tarea_exportar_documentos(self: object, job_id: str) -> dict[str, object]:
    identificador = UUID(job_id)
    reintentos = int(getattr(getattr(self, "request", None), "retries", 0))

    async def ejecutar() -> tuple[dict[str, object], Exception | None]:
        try:
            return await _procesar(identificador), None
        except exports.FuenteDeExportacionCambio:
            return {"job_id": job_id, "status": "FAILED", "error": "SOURCE_CHANGED"}, None
        except Exception as error:
            if reintentos < 3 and await _reprogramar(identificador):
                return {}, error
            raise
        finally:
            await get_engine().dispose()

    resultado, error_reintentable = asyncio.run(ejecutar())
    if error_reintentable is not None:
        raise self.retry(  # type: ignore[attr-defined]
            exc=error_reintentable,
            countdown=2 ** (reintentos + 1),
        ) from error_reintentable
    return resultado


def encolar_exportacion(job_id: UUID) -> None:
    tarea_exportar_documentos.delay(str(job_id))


@celery_app.task(name="documents.expire_exports")
def tarea_expirar_exportaciones() -> dict[str, int]:
    async def ejecutar() -> int:
        try:
            async with get_sessionmaker()() as session:
                total = await exports.expirar(session)
                await session.commit()
                return total
        finally:
            await get_engine().dispose()

    return {"expired": asyncio.run(ejecutar())}

"""Procesamiento asíncrono e idempotente de documentos."""

import asyncio
from uuid import UUID

from botocore.exceptions import BotoCoreError

from app.core.database import get_engine, get_sessionmaker
from app.modules.documents.service import procesar_documento
from app.workers.app import celery_app


async def _procesar(document_id: UUID) -> dict[str, object]:
    try:
        async with get_sessionmaker()() as session:
            resultado = await procesar_documento(session, document_id=document_id)
            await session.commit()
        return {
            "document_id": str(resultado.document_id),
            "status": resultado.upload_status,
            "size_bytes": resultado.size_bytes,
        }
    finally:
        # `asyncio.run()` crea un loop por tarea. El pool debe cerrarse antes
        # de que ese mismo loop termine; hacerlo en otro `asyncio.run()` deja
        # conexiones asyncpg ligadas a un loop ya cerrado.
        await get_engine().dispose()


@celery_app.task(
    bind=True,
    name="documents.process",
    autoretry_for=(BotoCoreError, OSError, TimeoutError),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=5,
)
def tarea_procesar_documento(self: object, document_id: str) -> dict[str, object]:
    return asyncio.run(_procesar(UUID(document_id)))


def encolar_procesamiento(document_id: UUID) -> None:
    tarea_procesar_documento.delay(str(document_id))

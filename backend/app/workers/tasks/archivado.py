"""Worker de recompresión de documentos archivados (ADR-0007)."""

import asyncio

from app.core.database import get_engine, get_sessionmaker
from app.modules.documents import archivado
from app.workers.app import celery_app


@celery_app.task(name="documents.recompress_archived")
def tarea_recomprimir_archivados() -> dict[str, int]:
    async def ejecutar() -> int:
        try:
            async with get_sessionmaker()() as session:
                total = await archivado.recomprimir_pendientes(session)
                await session.commit()
                return total
        finally:
            await get_engine().dispose()

    return {"recompressed": asyncio.run(ejecutar())}

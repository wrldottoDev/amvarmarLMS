"""Worker de archivado de cargas (ADR-0007)."""

import asyncio

from app.core.database import get_engine, get_sessionmaker
from app.modules.shipments import service
from app.workers.app import celery_app


@celery_app.task(name="shipments.archive_pending")
def tarea_archivar_cargas() -> dict[str, int]:
    async def ejecutar() -> int:
        try:
            async with get_sessionmaker()() as session:
                total = await service.archivar_pendientes(session)
                await session.commit()
                return total
        finally:
            await get_engine().dispose()

    return {"archived": asyncio.run(ejecutar())}

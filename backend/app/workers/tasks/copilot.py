"""Worker de vencimiento de propuestas del asistente (ADR-0012, Fase 7)."""

import asyncio

from app.core.database import get_engine, get_sessionmaker
from app.modules.copilot import propuestas
from app.workers.app import celery_app


@celery_app.task(name="copilot.expire_proposals")
def tarea_expirar_propuestas() -> dict[str, int]:
    async def ejecutar() -> int:
        try:
            async with get_sessionmaker()() as session:
                total = await propuestas.expirar(session)
                await session.commit()
                return total
        finally:
            await get_engine().dispose()

    return {"expired": asyncio.run(ejecutar())}

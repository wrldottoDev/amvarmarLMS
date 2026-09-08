"""Worker del outbox (Paso 4.1).

Toma los eventos pendientes y los entrega. La lógica de reclamo, backoff y
agotamiento vive en `app.modules.audit.outbox`; acá solo está el enganche con
Celery y el registro de manejadores por tipo de evento.

El registro de manejadores vive en `app.modules.notifications.handlers`: acá
solo está el enganche con Celery y la ligadura de la sesión del lote.
"""

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_engine, get_sessionmaker
from app.core.logging import obtener_logger
from app.modules.audit.outbox import EventoPendiente, Manejador, ResultadoLote, procesar_lote
from app.modules.notifications.handlers import POR_EVENTO
from app.workers.app import celery_app

_log = obtener_logger("outbox_worker")

LOTE = 50


def construir_manejador(session: AsyncSession) -> Manejador:
    """Liga la sesión del lote a los manejadores del catálogo.

    Los manejadores necesitan la sesión para escribir las notificaciones en la
    MISMA transacción que marca el evento como entregado. Si escribieran en una
    sesión propia, un corte podría dejar el aviso creado y el evento pendiente,
    y el reintento generaría un segundo aviso.
    """

    async def despachar(evento: EventoPendiente) -> None:
        manejador = POR_EVENTO.get(evento.event_type)

        if manejador is None:
            # Sin manejador no se reintenta: el evento queda entregado y con su
            # rastro en el log. Reintentar algo que ningún código sabe procesar
            # no lo acerca a entregarse, solo consume los seis intentos y
            # termina en FAILED.
            _log.info(
                "outbox_evento_sin_manejador",
                evento_id=str(evento.id),
                event_type=evento.event_type,
            )
            return

        await manejador(session, evento)

    return despachar


async def _ejecutar_lote() -> ResultadoLote:
    try:
        async with get_sessionmaker()() as session:
            resultado = await procesar_lote(session, construir_manejador(session), limite=LOTE)
            # El commit va acá y no dentro del lote: reclamo, entrega y marcado
            # comparten transacción, así que un corte deja las filas pendientes en
            # vez de marcadas sin haberse entregado.
            await session.commit()
        return resultado
    finally:
        await get_engine().dispose()


@celery_app.task(name="outbox.procesar_pendientes")
def tarea_procesar_pendientes() -> dict[str, int]:
    resultado = asyncio.run(_ejecutar_lote())

    return {
        "entregados": resultado.entregados,
        "reintentar": resultado.reintentar,
        "agotados": resultado.agotados,
    }

"""Logs estructurados en JSON.

Una línea JSON por evento, con `request_id` en todas: es lo que permite seguir
una petición completa (API, y más adelante worker y proveedor externo) cuando
algo falla en producción.
"""

import logging
import sys
from contextvars import ContextVar

import structlog
from structlog.typing import EventDict, WrappedLogger

# ContextVar y no un parámetro: así cualquier log dentro del request lleva el
# `request_id` sin que haya que pasarlo por toda la pila de llamadas.
request_id_actual: ContextVar[str | None] = ContextVar("request_id_actual", default=None)


def _inyectar_request_id(_logger: WrappedLogger, _nombre: str, evento: EventDict) -> EventDict:
    request_id = request_id_actual.get()
    if request_id is not None:
        evento["request_id"] = request_id
    return evento


def configurar_logging(*, debug: bool) -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=logging.INFO)

    procesadores: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        _inyectar_request_id,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    # En local, consola coloreada y legible. En cualquier otro entorno, JSON:
    # es lo que consume el agregador de logs.
    procesadores.append(
        structlog.dev.ConsoleRenderer() if debug else structlog.processors.JSONRenderer()
    )

    structlog.configure(
        processors=procesadores,
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def obtener_logger(nombre: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(nombre)  # type: ignore[no-any-return]

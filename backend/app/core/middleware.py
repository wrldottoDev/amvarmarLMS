"""Middleware transversal: request_id y log de acceso."""

import time
import uuid

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.core.logging import obtener_logger, request_id_actual

HEADER_REQUEST_ID = "X-Request-ID"

_log = obtener_logger("acceso")


def _request_id_entrante(request: Request) -> str:
    """Reutiliza el id del cliente si es un UUID válido; si no, genera uno.

    Aceptar cualquier string permitiría inyectar saltos de línea en los logs
    (log injection) o correlacionar peticiones ajenas con un id fabricado.
    """
    recibido = request.headers.get(HEADER_REQUEST_ID)
    if recibido:
        try:
            return str(uuid.UUID(recibido))
        except ValueError:
            pass
    return str(uuid.uuid4())


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = _request_id_entrante(request)
        request.state.request_id = request_id
        token = request_id_actual.set(request_id)

        inicio = time.perf_counter()
        try:
            respuesta = await call_next(request)
        finally:
            duracion_ms = (time.perf_counter() - inicio) * 1000

        respuesta.headers[HEADER_REQUEST_ID] = request_id

        _log.info(
            "request",
            metodo=request.method,
            # Ruta normalizada (`/shipments/{id}`), no la concreta: agrupar por
            # métrica sería imposible con un id distinto en cada línea.
            ruta=_ruta_normalizada(request),
            status=respuesta.status_code,
            duracion_ms=round(duracion_ms, 2),
            actor_user_id=getattr(request.state, "actor_user_id", None),
        )

        request_id_actual.reset(token)
        return respuesta


def _ruta_normalizada(request: Request) -> str:
    ruta = request.scope.get("route")
    return getattr(ruta, "path", request.url.path)


def registrar_middleware(app: FastAPI) -> None:
    app.add_middleware(RequestIdMiddleware)

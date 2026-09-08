"""Métricas HTTP: latencia y tasa de error (Paso 4.3).

Se instrumenta a mano en vez de usar el instrumentador por defecto para
controlar dos cosas que importan:

- **La etiqueta de ruta es la plantilla** (`/shipments/{shipment_id}`), no la
  URL concreta. Con la URL concreta cada carga sería una serie temporal
  distinta y Prometheus se quedaría sin memoria en semanas.
- **Los buckets del histograma** están puestos alrededor del objetivo del
  proyecto (p95 < 500 ms). Los buckets por defecto saltan de 0.5 a 1 s, y con
  eso no se puede distinguir 510 ms de 990 ms — justo el rango donde se decide
  si el objetivo se cumple.
"""

import time

from fastapi import FastAPI, Request, Response
from prometheus_client import Counter, Histogram
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.core.observability.metrics import REGISTRO

peticion_duracion = Histogram(
    "amvarmar_http_peticion_duracion_segundos",
    "Duración de las peticiones HTTP.",
    ["metodo", "ruta"],
    buckets=(0.01, 0.05, 0.1, 0.2, 0.3, 0.5, 0.75, 1.0, 2.0, 5.0, 10.0),
    registry=REGISTRO,
)

peticion_total = Counter(
    "amvarmar_http_peticion_total",
    "Peticiones HTTP por resultado.",
    ["metodo", "ruta", "codigo"],
    registry=REGISTRO,
)


def _ruta(request: Request) -> str:
    """Plantilla de la ruta; `desconocida` si no coincide con ninguna.

    Devolver la URL cruda cuando no hay coincidencia abriría la puerta a que
    cualquiera cree series temporales infinitas pidiendo rutas inexistentes.
    """
    ruta = request.scope.get("route")
    plantilla = getattr(ruta, "path", None)
    return str(plantilla) if plantilla else "desconocida"


class MetricasHttpMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        inicio = time.perf_counter()

        try:
            respuesta = await call_next(request)
        except Exception:
            # Una excepción sin manejar es un 500 para quien llama: no contarla
            # dejaría el peor caso fuera de la tasa de error.
            _registrar(request, "500", time.perf_counter() - inicio)
            raise

        _registrar(request, str(respuesta.status_code), time.perf_counter() - inicio)
        return respuesta


def _registrar(request: Request, codigo: str, duracion: float) -> None:
    # `/metrics` no se mide a sí mismo: cada raspado inflaría los percentiles
    # con una petición que no representa uso real.
    if request.url.path == "/metrics":
        return

    etiquetas = {"metodo": request.method, "ruta": _ruta(request)}
    peticion_duracion.labels(**etiquetas).observe(duracion)
    peticion_total.labels(**etiquetas, codigo=codigo).inc()


def registrar_metricas_http(app: FastAPI) -> None:
    app.add_middleware(MetricasHttpMiddleware)

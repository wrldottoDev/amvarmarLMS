"""Trazas OpenTelemetry: API → base de datos → worker (Paso 4.3).

Solo se instrumenta si hay un colector configurado. Exportar a un destino que
no existe llena los logs de errores de conexión y no aporta ninguna traza, así
que sin `OTEL_EXPORTER_ENDPOINT` la instrumentación no se activa.
"""

from fastapi import FastAPI

from app.core.config import get_settings
from app.core.logging import obtener_logger

_log = obtener_logger("trazas")


def configurar_trazas(app: FastAPI) -> bool:
    """Activa las trazas si hay colector. Devuelve si quedaron activas."""
    settings = get_settings()

    if not settings.otel_exporter_endpoint:
        return False

    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    from app.core.database import get_engine

    proveedor = TracerProvider(
        resource=Resource.create(
            {
                "service.name": settings.otel_service_name,
                "deployment.environment": settings.environment,
            }
        )
    )
    # En lote y no uno por uno: exportar de forma síncrona sumaría la latencia
    # del colector a la de cada petición.
    proveedor.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_exporter_endpoint))
    )
    trace.set_tracer_provider(proveedor)

    FastAPIInstrumentor.instrument_app(
        app,
        # Sin esto, cada raspado de Prometheus genera una traza que no dice nada.
        excluded_urls="/metrics,/health/live,/health/ready",
    )
    SQLAlchemyInstrumentor().instrument(engine=get_engine().sync_engine)

    _log.info("trazas_activas", endpoint=settings.otel_exporter_endpoint)
    return True

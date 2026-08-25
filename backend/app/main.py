from fastapi import FastAPI

from app.api.health import router as health_router
from app.core.config import get_settings
from app.core.errors import registrar_manejadores
from app.core.logging import configurar_logging
from app.core.middleware import registrar_middleware
from app.core.observability.http import registrar_metricas_http
from app.core.observability.router import router as metrics_router
from app.core.observability.tracing import configurar_trazas
from app.modules.auth.router import me_router
from app.modules.auth.router import router as auth_router
from app.modules.dispatches.router import router as dispatches_router
from app.modules.documents.router import router as documents_router
from app.modules.notifications.router import router as notifications_router
from app.modules.shipments.dashboard_router import router as dashboard_router
from app.modules.shipments.router import router as shipments_router

# Falla rápido si falta configuración obligatoria, antes de aceptar tráfico.
settings = get_settings()
configurar_logging(debug=settings.debug)

app = FastAPI(
    title="AMVARMAR LMS API",
    version="0.1.0",
    description="API del sistema de gestión logística de AMVARMAR.",
)

registrar_middleware(app)
registrar_metricas_http(app)
registrar_manejadores(app)
configurar_trazas(app)

app.include_router(health_router)
app.include_router(metrics_router)
app.include_router(auth_router)
app.include_router(me_router)
app.include_router(shipments_router)
app.include_router(dashboard_router)
app.include_router(documents_router)
app.include_router(dispatches_router)
app.include_router(notifications_router)

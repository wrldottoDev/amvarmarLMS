"""Métricas de Prometheus (Paso 4.3).

Dos familias distintas:

- **Contadores de proceso**: los incrementa el código cuando pasa algo (un
  login fallido, un reuse de refresh, un upload rechazado). Viven en memoria del
  proceso; Prometheus suma lo de todas las réplicas.
- **Indicadores derivados de la base**: cuánto hay pendiente en el outbox,
  cuántas entregas fallidas. No se pueden contar en memoria porque el número no
  depende de este proceso sino del estado compartido, así que se consultan al
  raspar, con caché corta para que un raspado agresivo no castigue a la base.

Los nombres siguen la convención de Prometheus: sufijo `_total` en contadores,
unidad explícita en el nombre.
"""

import time
from collections.abc import Awaitable, Callable

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.database import get_engine
from app.core.logging import obtener_logger

_log = obtener_logger("metricas")

# Registro propio en vez del global: el global acumula colectores entre
# instancias de la app, y en las pruebas eso da errores de métrica duplicada.
REGISTRO = CollectorRegistry()


# --- Seguridad ---

login_fallido_total = Counter(
    "amvarmar_login_fallido_total",
    "Intentos de autenticación rechazados.",
    ["motivo"],
    registry=REGISTRO,
)

refresh_reuse_total = Counter(
    "amvarmar_refresh_reuse_total",
    "Reutilizaciones de refresh token detectadas. Cada una revoca una sesión.",
    registry=REGISTRO,
)

# --- Documentos ---

upload_rechazado_total = Counter(
    "amvarmar_upload_rechazado_total",
    "Subidas rechazadas por validación de contenido, tamaño o formato.",
    ["motivo"],
    registry=REGISTRO,
)

documento_infectado_total = Counter(
    "amvarmar_documento_infectado_total",
    "Documentos que el antivirus marcó como infectados.",
    registry=REGISTRO,
)

# --- Outbox y notificaciones ---

outbox_procesado_total = Counter(
    "amvarmar_outbox_procesado_total",
    "Eventos del outbox por resultado del intento.",
    ["resultado"],
    registry=REGISTRO,
)

notificacion_enviada_total = Counter(
    "amvarmar_notificacion_enviada_total",
    "Entregas de notificación por canal y resultado.",
    ["canal", "resultado"],
    registry=REGISTRO,
)

# --- Indicadores derivados de la base ---

outbox_pendiente = Gauge(
    "amvarmar_outbox_pendiente",
    "Eventos del outbox en PENDING listos para entregar.",
    registry=REGISTRO,
)

outbox_agotado = Gauge(
    "amvarmar_outbox_agotado",
    "Eventos del outbox en FAILED: agotaron sus reintentos y nadie los entregó.",
    registry=REGISTRO,
)

outbox_antiguedad_segundos = Gauge(
    "amvarmar_outbox_antiguedad_segundos",
    "Antigüedad del evento pendiente más viejo. Detecta un worker detenido "
    "aunque la cola sea corta.",
    registry=REGISTRO,
)

notificacion_fallida = Gauge(
    "amvarmar_notificacion_fallida",
    "Entregas de notificación en estado FAILED.",
    ["canal"],
    registry=REGISTRO,
)

documento_sin_escanear = Gauge(
    "amvarmar_documento_sin_escanear",
    "Documentos subidos que siguen esperando el antivirus.",
    registry=REGISTRO,
)

# Duración del propio raspado: si esta consulta se vuelve lenta, la métrica lo
# dice antes de que el raspado empiece a dar timeout.
raspado_duracion = Histogram(
    "amvarmar_raspado_duracion_segundos",
    "Tiempo de la consulta que alimenta los indicadores derivados.",
    registry=REGISTRO,
)


_CACHE_SEGUNDOS = 15.0
_ultimo_refresco = 0.0

_CONSULTA = """
    SELECT
        (SELECT count(*) FROM outbox_events
          WHERE status = 'PENDING' AND available_at <= now())         AS pendientes,
        (SELECT count(*) FROM outbox_events WHERE status = 'FAILED')  AS agotados,
        (SELECT COALESCE(EXTRACT(EPOCH FROM now() - min(created_at)), 0)
           FROM outbox_events WHERE status = 'PENDING')               AS antiguedad,
        (SELECT count(*) FROM notification_deliveries
          WHERE status = 'FAILED' AND channel = 'EMAIL')              AS correos_fallidos,
        (SELECT count(*) FROM documents
          WHERE scan_status = 'PENDING' AND deleted_at IS NULL)       AS sin_escanear
"""


async def refrescar_indicadores(*, forzar: bool = False, engine: AsyncEngine | None = None) -> None:
    """Recalcula los indicadores derivados de la base.

    Con caché de 15 segundos: varias réplicas raspadas cada 15 s no deben
    convertirse en una consulta por réplica por segundo. Si la base no responde,
    se deja el valor anterior y se registra: un raspado no puede tumbar
    `/metrics`, porque entonces se perdería también todo lo demás justo cuando
    más falta hace.
    """
    global _ultimo_refresco

    ahora = time.monotonic()
    if not forzar and ahora - _ultimo_refresco < _CACHE_SEGUNDOS:
        return

    try:
        with raspado_duracion.time():
            # Construir el motor va DENTRO del try: si falla (configuración
            # inválida, pool agotado), dejarlo fuera haría que `/metrics`
            # devolviera 500 y se perdieran también las métricas de proceso,
            # justo cuando más falta hacen. El motor se puede inyectar porque
            # las pruebas corren contra un contenedor.
            motor = engine if engine is not None else get_engine()
            async with motor.connect() as conexion:
                fila = (await conexion.execute(text(_CONSULTA))).one()
    except Exception as error:
        _log.warning("metricas_indicadores_no_disponibles", error=str(error))
        return

    outbox_pendiente.set(fila.pendientes)
    outbox_agotado.set(fila.agotados)
    outbox_antiguedad_segundos.set(float(fila.antiguedad))
    notificacion_fallida.labels(canal="EMAIL").set(fila.correos_fallidos)
    documento_sin_escanear.set(fila.sin_escanear)
    _ultimo_refresco = ahora


def reiniciar_cache() -> None:
    """Invalida la caché del raspado. Para las pruebas."""
    global _ultimo_refresco
    _ultimo_refresco = 0.0


# Firma que espera el endpoint: se define acá para que el router no importe
# nada de SQLAlchemy.
Refrescador = Callable[[], Awaitable[None]]

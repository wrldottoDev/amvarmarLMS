"""Endpoint de métricas (Paso 4.3).

`/metrics` no es público. Expone nombres de rutas internas, volumen de usuarios
y patrones de fallo de login: reconocimiento gratis para quien quiera atacar el
sistema. Se protege con un token dedicado, distinto del JWT, porque quien raspa
es Prometheus y no una persona con sesión.

Sin token configurado, el endpoint queda deshabilitado fuera de local. Es fail
closed a propósito: un despliegue que olvida la variable no debe terminar
publicando las métricas.
"""

import hmac

from fastapi import APIRouter, Header, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.core.config import get_settings
from app.core.observability import metrics

router = APIRouter(tags=["observabilidad"])


def _token_valido(recibido: str | None) -> bool:
    settings = get_settings()
    esperado = settings.metrics_token

    if esperado is None:
        # Solo en local, donde no hay nada que proteger y pedir un token haría
        # que nadie mire las métricas durante el desarrollo.
        return settings.environment == "local"

    if not recibido:
        return False

    prefijo = "Bearer "
    valor = recibido[len(prefijo) :] if recibido.startswith(prefijo) else recibido
    # Comparación en tiempo constante: comparar con `==` filtra el token por el
    # tiempo de respuesta, carácter a carácter.
    return hmac.compare_digest(valor, esperado)


@router.get("/metrics", include_in_schema=False)
async def metricas(authorization: str | None = Header(default=None)) -> Response:
    if not get_settings().metricas_habilitadas:
        # 404 y no 403: que el endpoint exista ya es información.
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    if not _token_valido(authorization):
        return Response(status_code=status.HTTP_401_UNAUTHORIZED)

    await metrics.refrescar_indicadores()

    return Response(
        content=generate_latest(metrics.REGISTRO),
        media_type=CONTENT_TYPE_LATEST,
    )

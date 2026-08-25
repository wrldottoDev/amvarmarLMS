"""Formato único de error para toda la API.

Un solo esquema en todas las respuestas de error permite que el cliente
TypeScript generado desde OpenAPI (Fase F) tenga un tipo de error, no uno por
endpoint.

El `message` va en español y es para el usuario final: nunca lleva nombres de
tabla, trazas, ni el motivo interno por el que falló una autorización.
"""

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ErrorDeAplicacion(Exception):
    """Error con código estable, pensado para que el cliente lo interprete.

    El `code` es contrato público: el frontend puede ramificar sobre él. El
    `message` puede cambiar de redacción sin romper nada.
    """

    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str = "ERROR_DE_APLICACION"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or []
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code


class NoAutenticado(ErrorDeAplicacion):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "NO_AUTENTICADO"


class RecursoNoEncontrado(ErrorDeAplicacion):
    """También se usa cuando el recurso existe pero es de otra empresa.

    Responder 403 confirmaría su existencia, que ya es información sobre otra
    empresa. Desde afuera, ajeno e inexistente deben ser indistinguibles.
    """

    status_code = status.HTTP_404_NOT_FOUND
    code = "RECURSO_NO_ENCONTRADO"


class Conflicto(ErrorDeAplicacion):
    status_code = status.HTTP_409_CONFLICT
    code = "CONFLICTO"


class ReglaDeNegocioViolada(ErrorDeAplicacion):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "REGLA_DE_NEGOCIO_VIOLADA"


class DemasiadasSolicitudes(ErrorDeAplicacion):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "DEMASIADAS_SOLICITUDES"

    def __init__(self, message: str, *, retry_after_segundos: int) -> None:
        super().__init__(message)
        self.retry_after_segundos = retry_after_segundos


# Códigos por status HTTP, para errores que no nacen de ErrorDeAplicacion
# (los que levanta el propio framework).
_CODIGOS_POR_STATUS = {
    status.HTTP_400_BAD_REQUEST: "SOLICITUD_INVALIDA",
    status.HTTP_401_UNAUTHORIZED: "NO_AUTENTICADO",
    status.HTTP_403_FORBIDDEN: "SIN_PERMISO",
    status.HTTP_404_NOT_FOUND: "RECURSO_NO_ENCONTRADO",
    status.HTTP_405_METHOD_NOT_ALLOWED: "METODO_NO_PERMITIDO",
    status.HTTP_409_CONFLICT: "CONFLICTO",
    status.HTTP_422_UNPROCESSABLE_CONTENT: "PAYLOAD_INVALIDO",
    status.HTTP_429_TOO_MANY_REQUESTS: "DEMASIADAS_SOLICITUDES",
    status.HTTP_500_INTERNAL_SERVER_ERROR: "ERROR_INTERNO",
}


def construir_respuesta_error(
    *,
    status_code: int,
    code: str,
    message: str,
    request_id: str | None,
    details: list[dict[str, Any]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        headers=headers,
        content={
            "error": {
                "code": code,
                "message": message,
                "details": details or [],
                "request_id": request_id,
            }
        },
    )


def registrar_manejadores(app: FastAPI) -> None:
    @app.exception_handler(ErrorDeAplicacion)
    async def _error_de_aplicacion(request: Request, exc: ErrorDeAplicacion) -> JSONResponse:
        headers = None
        if isinstance(exc, DemasiadasSolicitudes):
            headers = {"Retry-After": str(exc.retry_after_segundos)}

        return construir_respuesta_error(
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            request_id=getattr(request.state, "request_id", None),
            details=exc.details,
            headers=headers,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return construir_respuesta_error(
            status_code=exc.status_code,
            code=_CODIGOS_POR_STATUS.get(exc.status_code, "ERROR"),
            message=str(exc.detail),
            request_id=getattr(request.state, "request_id", None),
            headers=dict(exc.headers) if exc.headers else None,
        )

    @app.exception_handler(RequestValidationError)
    async def _validacion(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Se exponen los errores de forma del payload (qué campo falta, de qué
        # tipo debe ser). No revelan nada interno y sin ellos el cliente no
        # puede corregir la petición.
        detalles = [
            {
                "campo": ".".join(str(parte) for parte in error["loc"]),
                "problema": error["msg"],
                "tipo": error["type"],
            }
            for error in exc.errors()
        ]
        return construir_respuesta_error(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="PAYLOAD_INVALIDO",
            message="El cuerpo de la solicitud no es válido.",
            request_id=getattr(request.state, "request_id", None),
            details=jsonable_encoder(detalles),
        )

    @app.exception_handler(Exception)
    async def _no_controlado(request: Request, exc: Exception) -> JSONResponse:
        # Nunca se filtra la excepción al cliente: el mensaje real va al log,
        # y el `request_id` es lo que permite correlacionarlos.
        return construir_respuesta_error(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="ERROR_INTERNO",
            message="Ocurrió un error interno. Intente de nuevo más tarde.",
            request_id=getattr(request.state, "request_id", None),
        )

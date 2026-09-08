"""Cliente del proveedor de IA (ADR-0012).

NO importa SQLAlchemy ni nada de `app.core.database`. Si algún día algo acá
necesitara la base, la separación entre "habla con el proveedor" y "toca la
base" ya se rompió, y el proveedor pasaría a ser frontera de autorización —
exactamente lo que el ADR prohíbe.

`ProveedorIA` es el `Protocol` que usa `service.py`. Las pruebas inyectan un
proveedor falso que lo implementa; ninguna prueba de la suite normal llama a
la API real (ver `tests/copilot/proveedor_falso.py`).
"""

from __future__ import annotations

import json
import time
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Protocol

from openai import AsyncOpenAI, OpenAIError

from app.core.config import get_settings


@dataclass(frozen=True)
class LlamadaHerramienta:
    call_id: str
    nombre: str
    argumentos_json: str


@dataclass(frozen=True)
class DescripcionFactura:
    """Lo que el proveedor pudo leer de una factura (imagen o PDF).

    Todo campo es `None` cuando el documento no lo trae — nunca se inventa un
    valor para completar el esquema (verificado contra la API real: con
    `strict=True` y tipo `["string","null"]`/`["number","null"]` el modelo
    responde `null` en vez de alucinar, ver ADR-0012)."""

    numero_guia: str | None
    proveedor: str | None
    monto: float | None
    moneda: str | None
    cliente: str | None


@dataclass(frozen=True)
class RespuestaProveedor:
    id: str
    texto: str | None
    llamadas_herramientas: tuple[LlamadaHerramienta, ...]
    # Los ítems de salida crudos (mensaje, razonamiento, function_call...) tal
    # como los devolvió el proveedor. `service.py` los reenvía en la próxima
    # vuelta del mismo turno: con `store=false` no hay `previous_response_id`
    # que valga (ver ADR-0012, enmienda 2026-09), así que la única forma de
    # que el modelo "recuerde" que pidió una herramienta es reenviarle su
    # propia salida completa junto con el resultado.
    items_salida: tuple[dict[str, Any], ...]
    tokens_entrada: int
    tokens_salida: int


class ProveedorNoDisponible(Exception):
    """Falta la clave, el circuit breaker está abierto, o el proveedor falló.

    `capabilities` responde `disponible:false` ante esto — nunca 500. El resto
    del LMS no depende del proveedor de IA para funcionar.
    """


class ProveedorIA(Protocol):
    async def responder(
        self, *, entrada: list[dict[str, Any]], herramientas: list[dict[str, Any]]
    ) -> RespuestaProveedor: ...

    async def describir_factura(
        self, *, media_type: str, contenido_base64: str
    ) -> DescripcionFactura: ...


# ContextVar y no un parámetro de `EjecutorHerramienta`: así el ejecutor de
# `procesar_factura_ocr` (el único que necesita hablarle al proveedor, para
# leer un documento) lo toma acá en vez de que las otras ocho herramientas
# cargaran con un parámetro que nunca usan — mismo patrón que
# `request_id_actual` en `core/logging.py`. `service.procesar_turno` lo fija
# al principio del turno, con el proveedor ya resuelto por la fábrica de
# `router.py` (real o falso, según el entorno).
proveedor_actual: ContextVar[ProveedorIA] = ContextVar("proveedor_actual")


class _CircuitBreaker:
    """En memoria de proceso: alcanza para un backend de un solo proceso por
    worker. Si el despliegue pasa a múltiples workers sin estado compartido,
    cada worker abre su propio breaker — conservador, no un problema."""

    def __init__(self, *, fallos_para_abrir: int, segundos_abierto: float) -> None:
        self._fallos_para_abrir = fallos_para_abrir
        self._segundos_abierto = segundos_abierto
        self._fallos_seguidos = 0
        self._abierto_hasta: float | None = None

    def disponible(self) -> bool:
        # Medio abierto pasado `_abierto_hasta`: se deja pasar un intento para
        # ver si se recuperó. Si ese intento falla, `registrar_fallo` lo
        # vuelve a abrir.
        return self._abierto_hasta is None or time.monotonic() >= self._abierto_hasta

    def registrar_exito(self) -> None:
        self._fallos_seguidos = 0
        self._abierto_hasta = None

    def registrar_fallo(self) -> None:
        self._fallos_seguidos += 1
        if self._fallos_seguidos >= self._fallos_para_abrir:
            self._abierto_hasta = time.monotonic() + self._segundos_abierto


_breaker: _CircuitBreaker | None = None


def _obtener_breaker() -> _CircuitBreaker:
    global _breaker
    if _breaker is None:
        settings = get_settings()
        _breaker = _CircuitBreaker(
            fallos_para_abrir=settings.copilot_breaker_fallos_para_abrir,
            segundos_abierto=settings.copilot_breaker_segundos_abierto,
        )
    return _breaker


def _reiniciar_breaker_para_pruebas() -> None:
    """Solo para tests: el breaker es estado de módulo y una prueba no puede
    dejarlo abierto para la siguiente."""
    global _breaker
    _breaker = None


def breaker_disponible() -> bool:
    """Para `GET /copilot/capabilities`: ver el estado sin gastar un intento
    ni instanciar un cliente real."""
    return _obtener_breaker().disponible()


class ProveedorOpenAI:
    """Implementación real. Responses API, `store=false`, sin `parallel_tool_calls`.

    NO envía `temperature`: verificado contra la API real que `gpt-5.6-luna`
    (modelo de razonamiento) la rechaza con 400. El control de determinismo es
    `reasoning.effort`.
    """

    def __init__(self) -> None:
        settings = get_settings()
        if not settings.openai_api_key:
            raise ProveedorNoDisponible("OPENAI_API_KEY no configurada.")
        self._cliente = AsyncOpenAI(
            api_key=settings.openai_api_key, timeout=settings.copilot_timeout_segundos
        )
        self._modelo = settings.copilot_model
        self._reasoning_effort = settings.copilot_reasoning_effort
        self._max_tokens_salida = settings.copilot_max_tokens_salida

    async def responder(
        self, *, entrada: list[dict[str, Any]], herramientas: list[dict[str, Any]]
    ) -> RespuestaProveedor:
        """`entrada` es el array completo — system prompt, historial, y (si
        esta es una vuelta posterior del mismo turno) los ítems de salida y
        resultados de herramienta anteriores. Sin estado del lado del
        proveedor (`store=false`), esta llamada es la única fuente de verdad
        de lo que el modelo "sabe" en este momento."""
        breaker = _obtener_breaker()
        if not breaker.disponible():
            raise ProveedorNoDisponible("El proveedor está temporalmente deshabilitado.")

        try:
            # `input`, `tools` y `reasoning` del SDK son uniones de TypedDicts
            # muy específicos; los dicts genéricos que arma este módulo (para
            # no acoplar el resto del código a los tipos del SDK) no calzan
            # estructuralmente aunque el valor en runtime sea válido —
            # verificado contra la API real (ver ADR-0012, enmienda 2026-09).
            respuesta = await self._cliente.responses.create(  # type: ignore[call-overload]
                model=self._modelo,
                input=entrada,
                tools=herramientas,
                reasoning={"effort": self._reasoning_effort},
                max_output_tokens=self._max_tokens_salida,
                store=False,
                parallel_tool_calls=False,
            )
        except OpenAIError as error:
            breaker.registrar_fallo()
            raise ProveedorNoDisponible(f"El proveedor de IA no respondió: {error}") from error

        breaker.registrar_exito()

        # `model_dump()` trae campos de SOLO-SALIDA (p.ej. `status`) que la
        # API rechaza si se los reenvía tal cual dentro de `input` en la
        # próxima vuelta — verificado contra la API real (ver ADR-0012,
        # enmienda 2026-09): "Unknown parameter: 'input[N].status'".
        items_salida = tuple(
            {k: v for k, v in item.model_dump().items() if k != "status"}
            for item in respuesta.output
        )
        llamadas = tuple(
            LlamadaHerramienta(
                call_id=item.call_id, nombre=item.name, argumentos_json=item.arguments
            )
            for item in respuesta.output
            if item.type == "function_call"
        )

        uso = respuesta.usage
        return RespuestaProveedor(
            id=respuesta.id,
            texto=respuesta.output_text or None,
            llamadas_herramientas=llamadas,
            items_salida=items_salida,
            tokens_entrada=uso.input_tokens if uso else 0,
            tokens_salida=uso.output_tokens if uso else 0,
        )

    async def describir_factura(
        self, *, media_type: str, contenido_base64: str
    ) -> DescripcionFactura:
        """Una sola llamada, fuera del bucle de tool calling (ADR-0012, Fase
        6): a diferencia de `responder()`, esto nunca se reenvía en una
        vuelta siguiente, así que el archivo no se re-envía turno tras turno
        bajo `store=false` — el resultado (chico) es lo único que entra a la
        conversación, vía la propuesta que arma el ejecutor.

        Acepta imagen o PDF — verificado contra la API real que
        `gpt-5.6-luna` lee ambos (`input_image` / `input_file`) y que
        `text.format: json_schema` con `strict=True` devuelve `null` en vez
        de inventar un valor que el documento no trae."""
        breaker = _obtener_breaker()
        if not breaker.disponible():
            raise ProveedorNoDisponible("El proveedor está temporalmente deshabilitado.")

        if media_type.startswith("image/"):
            parte_archivo: dict[str, Any] = {
                "type": "input_image",
                "image_url": f"data:{media_type};base64,{contenido_base64}",
            }
        elif media_type == "application/pdf":
            parte_archivo = {
                "type": "input_file",
                "filename": "factura.pdf",
                "file_data": f"data:{media_type};base64,{contenido_base64}",
            }
        else:
            raise ProveedorNoDisponible(f"Tipo de archivo no soportado para lectura: {media_type}")

        try:
            respuesta = await self._cliente.responses.create(  # type: ignore[call-overload]
                model=self._modelo,
                input=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": (
                                    "Extraé de esta factura: número de guía, proveedor, "
                                    "monto, moneda y cliente. Si un dato no aparece, "
                                    "dejalo en null — no lo inventes."
                                ),
                            },
                            parte_archivo,
                        ],
                    }
                ],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "descripcion_factura",
                        "strict": True,
                        "schema": {
                            "type": "object",
                            "properties": {
                                "numero_guia": {"type": ["string", "null"]},
                                "proveedor": {"type": ["string", "null"]},
                                "monto": {"type": ["number", "null"]},
                                "moneda": {"type": ["string", "null"]},
                                "cliente": {"type": ["string", "null"]},
                            },
                            "required": [
                                "numero_guia",
                                "proveedor",
                                "monto",
                                "moneda",
                                "cliente",
                            ],
                            "additionalProperties": False,
                        },
                    }
                },
                reasoning={"effort": self._reasoning_effort},
                max_output_tokens=self._max_tokens_salida,
                store=False,
            )
        except OpenAIError as error:
            breaker.registrar_fallo()
            raise ProveedorNoDisponible(f"El proveedor de IA no respondió: {error}") from error

        breaker.registrar_exito()

        datos = json.loads(respuesta.output_text)
        return DescripcionFactura(
            numero_guia=datos["numero_guia"],
            proveedor=datos["proveedor"],
            monto=datos["monto"],
            moneda=datos["moneda"],
            cliente=datos["cliente"],
        )

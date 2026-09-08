"""Proveedor de IA determinístico, solo para Playwright (ADR-0012, Fase 3).

Nunca llama a la API real. `_fabrica_proveedor` (`copilot/router.py`) lo usa en
vez de `ProveedorOpenAI` cuando `settings.copilot_proveedor_falso` está
activo — y `Settings` (`app/core/config.py`) rechaza esa combinación fuera de
`environment=local`, así que no hay forma de que esto llegue a producción por
una variable de entorno olvidada.

No "entiende" nada: mira el último mensaje del actor con una regla fija (¿trae
un código de carga SHP-YYYY-NNNNNN?) para que un test de Playwright pueda
predecir la respuesta sin control sobre un modelo real. Sin estado propio:
todo lo que necesita sale de `entrada`, igual que el proveedor real con
`store=false` (ver `provider.py`).
"""

from __future__ import annotations

import json
import re
from typing import Any
from uuid import uuid4

from app.modules.copilot.provider import DescripcionFactura, LlamadaHerramienta, RespuestaProveedor

_PATRON_CARGA = re.compile(r"\bSHP-\d{4}-\d{6}\b")

_TEXTO_SIN_CODIGO = (
    "Soy AMVI de pruebas (proveedor falso, sin IA real). "
    "Preguntame por un código de carga (SHP-2026-000123) para buscarlo."
)


def _id_falso(prefijo: str) -> str:
    return f"{prefijo}_falso_{uuid4().hex[:8]}"


def _respuesta_de_texto(texto: str) -> RespuestaProveedor:
    return RespuestaProveedor(
        id=_id_falso("resp"),
        texto=texto,
        llamadas_herramientas=(),
        items_salida=(),
        tokens_entrada=0,
        tokens_salida=0,
    )


def _ultimo_mensaje_de_usuario(entrada: list[dict[str, Any]]) -> str:
    for item in reversed(entrada):
        if item.get("role") == "user":
            return str(item.get("content") or "")
    return ""


def _ultimo_resultado_de_herramienta(entrada: list[dict[str, Any]]) -> dict[str, Any] | None:
    for item in reversed(entrada):
        if item.get("type") == "function_call_output":
            return dict(json.loads(item["output"]))
    return None


class ProveedorFalsoDeterministico:
    """Un tool call a `buscar_cargas` si el mensaje trae un código de carga,
    texto directo si no. La segunda vuelta (con el resultado ya en `entrada`)
    siempre cierra en texto — nunca entra en el bucle de tool calls."""

    async def responder(
        self, *, entrada: list[dict[str, Any]], herramientas: list[dict[str, Any]]
    ) -> RespuestaProveedor:
        resultado_previo = _ultimo_resultado_de_herramienta(entrada)
        if resultado_previo is not None:
            return _respuesta_de_texto(_texto_desde_resultado(resultado_previo))

        coincidencia = _PATRON_CARGA.search(_ultimo_mensaje_de_usuario(entrada))
        ofrece_buscar_cargas = any(h.get("name") == "buscar_cargas" for h in herramientas)
        if coincidencia and ofrece_buscar_cargas:
            return _llamada_a_buscar_cargas(coincidencia.group(0))

        return _respuesta_de_texto(_TEXTO_SIN_CODIGO)

    async def describir_factura(
        self, *, media_type: str, contenido_base64: str
    ) -> DescripcionFactura:
        """Determinístico: siempre el mismo resultado, sin mirar el archivo —
        Playwright no necesita variar esto, solo que el flujo completo
        (preview → propuesta → confirmar) corra sin tocar la API real."""
        return DescripcionFactura(
            numero_guia="INV-FALSA-0001",
            proveedor="Proveedor de pruebas",
            monto=100.0,
            moneda="USD",
            cliente="Cliente de pruebas",
        )


def _llamada_a_buscar_cargas(codigo: str) -> RespuestaProveedor:
    call_id = _id_falso("call")
    argumentos_json = json.dumps({"q": codigo})
    item_salida = {
        "type": "function_call",
        "call_id": call_id,
        "name": "buscar_cargas",
        "arguments": argumentos_json,
    }
    return RespuestaProveedor(
        id=_id_falso("resp"),
        texto=None,
        llamadas_herramientas=(
            LlamadaHerramienta(
                call_id=call_id, nombre="buscar_cargas", argumentos_json=argumentos_json
            ),
        ),
        items_salida=(item_salida,),
        tokens_entrada=0,
        tokens_salida=0,
    )


def _texto_desde_resultado(resultado: dict[str, Any]) -> str:
    if "error" in resultado:
        return f"No pude consultar eso: {resultado['error']}"

    resultados = resultado.get("resultados") or []
    if not resultados:
        return "No encontré ninguna carga con ese código."

    primera = resultados[0]
    return f"La carga {primera['shipment_number']} está en estado {primera['estado']}."

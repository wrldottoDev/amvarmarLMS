"""Orquesta el ciclo de tool calling (ADR-0012).

NINGUNA escritura ocurre durante el turno del modelo. Una herramienta de clase
ESCRITURA solo puede terminar en una propuesta `PENDING` — nunca en un cambio
de datos. Confirmar es una llamada HTTP aparte (`router.confirmar`), con su
propio permiso revalidado y su `Idempotency-Key`.

Fase 3 conectó las de LECTURA (`copilot/executors.py`). Fase 4 conectó la
primera de ESCRITURA, `crear_prealerta_borrador`, y luego `procesar_factura_ocr`
(`executors_escritura.py` + `confirmaciones.py`), sobre la misma
infraestructura genérica de propuestas. Una herramienta permitida sin ejecutor
registrado responde con un error controlado, nunca con datos inventados ni
con un crash — así sigue funcionando cualquier ESCRITURA futura que todavía
no tenga ejecutor.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.modules.audit.models import Outcome
from app.modules.audit.service import registrar
from app.modules.copilot.esquemas_openai import esquemas_openai
from app.modules.copilot.executors import REGISTRO_EJECUTORES as _EJECUTORES_LECTURA
from app.modules.copilot.executors import EjecutorHerramienta
from app.modules.copilot.executors_escritura import (
    REGISTRO_EJECUTORES_ESCRITURA as _EJECUTORES_ESCRITURA,
)
from app.modules.copilot.provider import ProveedorIA, ProveedorNoDisponible
from app.modules.copilot.provider import proveedor_actual as _proveedor_actual
from app.modules.copilot.tools import (
    HERRAMIENTAS,
    ClaseHerramienta,
    herramientas_disponibles,
    puede_ejecutar,
)
from app.modules.rbac.service import PermisosEfectivos

# Un solo registro para `_resultado_de_llamada`: la clase LECTURA/ESCRITURA de
# la herramienta ya decidió qué hace su ejecutor (devolver datos o persistir
# una propuesta) — acá no hace falta bifurcar.
REGISTRO_EJECUTORES: dict[str, EjecutorHerramienta] = {
    **_EJECUTORES_LECTURA,
    **_EJECUTORES_ESCRITURA,
}


@dataclass(frozen=True)
class EventoSSE:
    evento: str
    datos: dict[str, Any]


async def _resultado_de_llamada(
    session: AsyncSession,
    *,
    nombre: str,
    argumentos_json: str,
    permisos: PermisosEfectivos,
    actor_user_id: UUID,
    company_id: UUID | None,
) -> dict[str, Any]:
    """Revalida, valida y ejecuta (o rechaza) una llamada a herramienta.

    Esta es la revalidación real (ADR-0012): un modelo puede emitir una
    llamada a una herramienta que no se le ofreció, así que no basta con
    haberla filtrado antes de armar la lista que vio el modelo.
    """
    definicion = HERRAMIENTAS.get(nombre)
    # Los argumentos YA son la forma "redactada" que pide el ADR (line 167):
    # el tipado de la herramienta, nunca el texto libre de la conversación.
    # Best-effort incluso si el modelo mandó un JSON roto — investigar qué
    # pasó es exactamente para lo que existe esta auditoría.
    try:
        argumentos_auditoria: Any = json.loads(argumentos_json)
    except json.JSONDecodeError:
        argumentos_auditoria = argumentos_json

    if definicion is None:
        resultado = {"error": "Esa herramienta no existe."}
        await registrar(
            session,
            action="copilot.tool.invoked",
            resource_type="copilot_tool",
            outcome=Outcome.FAILED,
            actor_user_id=actor_user_id,
            company_id=company_id,
            after_data={
                "herramienta": nombre,
                "motivo": "inexistente",
                "argumentos": argumentos_auditoria,
                "resultado": resultado,
            },
        )
        return resultado

    if not puede_ejecutar(nombre, permisos):
        resultado = {"error": "No tenés permiso para esa acción."}
        await registrar(
            session,
            action="copilot.tool.invoked",
            resource_type="copilot_tool",
            outcome=Outcome.DENIED,
            actor_user_id=actor_user_id,
            company_id=company_id,
            after_data={
                "herramienta": nombre,
                "motivo": "sin_permiso",
                "argumentos": argumentos_auditoria,
                "resultado": resultado,
            },
        )
        return resultado

    try:
        definicion.argumentos.model_validate_json(argumentos_json)
    except ValidationError:
        resultado = {"error": "Los argumentos no tienen la forma esperada."}
        await registrar(
            session,
            action="copilot.tool.invoked",
            resource_type="copilot_tool",
            outcome=Outcome.FAILED,
            actor_user_id=actor_user_id,
            company_id=company_id,
            after_data={
                "herramienta": nombre,
                "motivo": "argumentos_invalidos",
                "argumentos": argumentos_auditoria,
                "resultado": resultado,
            },
        )
        return resultado

    ejecutor = REGISTRO_EJECUTORES.get(nombre)
    if ejecutor is None:
        # No es un error del actor ni del modelo: la fundación está lista,
        # pero esta fase todavía no conectó el ejecutor de esta herramienta.
        resultado = {"error": "Esta función todavía no está disponible."}
        await registrar(
            session,
            action="copilot.tool.invoked",
            resource_type="copilot_tool",
            outcome=Outcome.FAILED,
            actor_user_id=actor_user_id,
            company_id=company_id,
            after_data={
                "herramienta": nombre,
                "motivo": "sin_ejecutor_registrado",
                "argumentos": argumentos_auditoria,
                "resultado": resultado,
            },
        )
        return resultado

    argumentos = json.loads(argumentos_json)
    resultado = await ejecutor(session, permisos, actor_user_id, company_id, argumentos)

    await registrar(
        session,
        action="copilot.tool.invoked",
        resource_type="copilot_tool",
        outcome=Outcome.SUCCESS,
        actor_user_id=actor_user_id,
        company_id=company_id,
        after_data={"herramienta": nombre, "argumentos": argumentos, "resultado": resultado},
    )
    return resultado


async def procesar_turno(
    session: AsyncSession,
    *,
    proveedor: ProveedorIA,
    actor_user_id: UUID,
    company_id: UUID | None,
    permisos: PermisosEfectivos,
    nombre_actor: str,
    es_cliente: bool,
    empresa_nombre: str | None,
    mensajes: list[dict[str, str]],
) -> AsyncIterator[EventoSSE]:
    """Un turno completo: habla con el proveedor, ejecuta las herramientas que
    pida (dentro del tope), y vuelve a hablarle con los resultados hasta que
    responda con texto o se agote el tope de vueltas.

    Sin `previous_response_id` (ADR-0012, enmienda 2026-09: `store=false` lo
    rompe). Cada vuelta reenvía el array `entrada` completo — incluidos los
    ítems de salida crudos de la vuelta anterior — porque es la única forma de
    que el modelo "recuerde" que pidió una herramienta cuando el proveedor no
    retiene nada del lado del servidor.
    """
    from app.modules.copilot.prompts import construir_system_prompt

    settings = get_settings()
    token_proveedor = _proveedor_actual.set(proveedor)

    if len(mensajes) > settings.copilot_max_mensajes_por_turno:
        yield EventoSSE(
            "error",
            {
                "code": "COPILOT_DEMASIADOS_MENSAJES",
                "message": "Hay demasiados mensajes en esta conversación.",
            },
        )
        return

    ofrecidas = herramientas_disponibles(permisos)
    esquemas = esquemas_openai(list(ofrecidas))
    system_prompt = construir_system_prompt(
        nombre_actor=nombre_actor, es_cliente=es_cliente, empresa=empresa_nombre
    )

    entrada: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        *({"role": m["rol"], "content": m["contenido"]} for m in mensajes),
    ]
    response_id_actual: str | None = None
    tokens_entrada_total = 0
    tokens_salida_total = 0
    agotado = True

    for _vuelta in range(settings.copilot_max_tool_calls_por_turno + 1):
        try:
            respuesta = await proveedor.responder(entrada=entrada, herramientas=esquemas)
        except ProveedorNoDisponible as error:
            yield EventoSSE("error", {"code": "COPILOT_NO_DISPONIBLE", "message": str(error)})
            return

        response_id_actual = respuesta.id
        tokens_entrada_total += respuesta.tokens_entrada
        tokens_salida_total += respuesta.tokens_salida

        if not respuesta.llamadas_herramientas:
            if respuesta.texto:
                yield EventoSSE("token", {"texto": respuesta.texto})
            agotado = False
            break

        entrada = [*entrada, *respuesta.items_salida]
        for llamada in respuesta.llamadas_herramientas:
            yield EventoSSE("herramienta", {"nombre": llamada.nombre, "estado": "ejecutando"})

            resultado = await _resultado_de_llamada(
                session,
                nombre=llamada.nombre,
                argumentos_json=llamada.argumentos_json,
                permisos=permisos,
                actor_user_id=actor_user_id,
                company_id=company_id,
            )
            entrada.append(
                {
                    "type": "function_call_output",
                    "call_id": llamada.call_id,
                    # `default=str`: los ejecutores devuelven filas de la base
                    # con UUID, datetime y Decimal, que `json` no serializa
                    # nativamente. Convertir a texto es correcto para lo que
                    # el modelo necesita leer, no para volver a parsear.
                    "output": json.dumps(resultado, ensure_ascii=False, default=str),
                }
            )
            # El resultado de una ESCRITURA es la `PropuestaAccion` completa
            # (id, campos, advertencias) — el modelo la recibe por el
            # `function_call_output` de arriba para poder narrarla, pero el
            # frontend necesita la estructura tal cual para dibujar la
            # tarjeta editable. Un `{"error": ...}` (sin ejecutor conectado,
            # o sin `company_id`) nunca creó una propuesta real: no hay nada
            # que la tarjeta pueda mostrar.
            definicion = HERRAMIENTAS.get(llamada.nombre)
            if (
                definicion is not None
                and definicion.clase is ClaseHerramienta.ESCRITURA
                and "error" not in resultado
            ):
                yield EventoSSE("propuesta", resultado)
            yield EventoSSE("herramienta", {"nombre": llamada.nombre, "estado": "completada"})

    if agotado:
        yield EventoSSE(
            "error",
            {
                "code": "COPILOT_LIMITE_HERRAMIENTAS",
                "message": "Se alcanzó el límite de acciones para este turno.",
            },
        )

    await session.commit()
    _proveedor_actual.reset(token_proveedor)

    yield EventoSSE(
        "fin",
        {
            "tokens_entrada": tokens_entrada_total,
            "tokens_salida": tokens_salida_total,
            "response_id": response_id_actual,
        },
    )

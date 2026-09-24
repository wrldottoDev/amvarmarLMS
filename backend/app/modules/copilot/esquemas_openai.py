"""Traduce un modelo Pydantic a un esquema de function calling estricto.

ADR-0012, enmienda 2026-09. Reemplaza a `esquema_para_proveedor()`, que emitía
el envoltorio de Chat Completions (`{"type":"function","function":{...}}`) y
un `parameters` que la Responses API rechaza con `strict=true`. Verificado
contra el SDK instalado y una llamada real: `additionalProperties` debe ser
`false` en todo objeto, y todos los campos deben estar en `required` — los
opcionales de Pydantic (`anyOf` con `null` y un `default`) tienen que pasar a
requeridos-nullable.

La documentación pública de OpenAI no publica la lista completa de keywords
que `strict` admite. En vez de adivinarla, este módulo emite solo el
subconjunto verificado como seguro: `type`, `properties`, `required`,
`additionalProperties`, `description`, `enum`. Descarta a propósito
`default`, `title`, `maxLength`, `minimum`, `maximum` y cualquier otra
restricción numérica o de longitud — confirmado con una llamada real que un
schema con esas restricciones también es aceptado, pero como el límite real
de lo aceptado no está documentado, mantener el generador en el subconjunto
mínimo lo hace más resistente a que el proveedor cambie qué acepta.

Los límites de Pydantic (`max_length`, `ge`, `le`, ...) NO desaparecen: siguen
viviendo en el modelo, que revalida los argumentos cuando vuelven del modelo.
El esquema que ve la IA es una pista, no la validación. Lo que el modelo
necesita saber sobre un límite se escribe en `description`.
"""

from typing import Any

from pydantic import BaseModel

from app.modules.copilot.tools import DefinicionHerramienta


class EsquemaNoSoportado(Exception):
    """Un campo del modelo Pydantic usa una forma que este generador no cubre.

    Mejor fallar temprano y explícito que emitir un esquema que la Responses
    API rechace en producción, o peor, que acepte pero interprete distinto de
    lo que el desarrollador esperaba.
    """


def _tipo_json(anotacion_tipo: str | list[str]) -> str | list[str]:
    return anotacion_tipo


def _limpiar_propiedad(bruta: dict[str, Any]) -> dict[str, Any]:
    """Reduce un fragmento de `properties` al subconjunto seguro para strict.

    Cubre los dos casos que aparecen en el catálogo hoy: un campo requerido
    simple (`{"type": "string", ...}`) y un campo opcional que Pydantic
    representa como `anyOf: [{tipo}, {"type": "null"}]` con `default: null`.
    Un campo opcional pasa a `type: [tipo, "null"]`, requerido igual que los
    demás — así es como se representa "opcional" en strict.
    """
    if "anyOf" in bruta:
        tipos: list[str] = []
        enum_valores: list[Any] | None = None
        for variante in bruta["anyOf"]:
            if "type" not in variante:
                raise EsquemaNoSoportado(
                    f"Variante de anyOf sin 'type' simple: {variante}. "
                    "Este generador no cubre uniones que no sean 'tipo | None'."
                )
            tipos.append(variante["type"])
            if "enum" in variante:
                enum_valores = variante["enum"]

        limpia: dict[str, Any] = {"type": _tipo_json(tipos)}
        if "description" in bruta:
            limpia["description"] = bruta["description"]
        if enum_valores is not None:
            limpia["enum"] = enum_valores
        return limpia

    tipo = bruta.get("type")
    if tipo is None:
        raise EsquemaNoSoportado(
            f"Propiedad sin 'type' ni 'anyOf': {bruta}. "
            "¿Es un modelo anidado o un $ref? Ese caso no está cubierto todavía."
        )
    if tipo == "object":
        raise EsquemaNoSoportado(
            "Objetos anidados no están cubiertos: ninguna herramienta del catálogo "
            "los usa hoy. Si agregás uno, extendé este generador y su prueba."
        )

    limpia = {"type": tipo}
    if "description" in bruta:
        limpia["description"] = bruta["description"]
    if "enum" in bruta:
        limpia["enum"] = bruta["enum"]
    if tipo == "array":
        if "items" not in bruta:
            raise EsquemaNoSoportado(f"Array sin 'items': {bruta}")
        limpia["items"] = _limpiar_propiedad(bruta["items"])
    return limpia


def _parametros_strict(modelo: type[BaseModel]) -> dict[str, Any]:
    bruto = modelo.model_json_schema()
    if "$defs" in bruto or "$ref" in bruto:
        raise EsquemaNoSoportado(
            f"{modelo.__name__} usa $defs/$ref (submodelo anidado). "
            "Ninguna herramienta del catálogo lo necesita hoy; si hace falta, "
            "este generador tiene que resolver las referencias primero."
        )

    propiedades_brutas = bruto.get("properties", {})
    propiedades = {
        nombre: _limpiar_propiedad(valor) for nombre, valor in propiedades_brutas.items()
    }

    return {
        "type": "object",
        "properties": propiedades,
        # TODOS los campos van en required — strict lo exige incluso para los
        # que Pydantic marca opcionales. La opcionalidad real está en que su
        # `type` incluye "null".
        "required": list(propiedades_brutas.keys()),
        "additionalProperties": False,
    }


def esquema_openai(definicion: DefinicionHerramienta) -> dict[str, Any]:
    """Forma PLANA que exige la Responses API — no el envoltorio de Chat Completions.

    Verificado contra `client.responses.create(tools=[...])` real: `name`,
    `parameters` y `strict` van al mismo nivel que `type`, no anidados bajo
    `function`.
    """
    return {
        "type": "function",
        "name": definicion.nombre,
        "description": definicion.descripcion,
        "parameters": _parametros_strict(definicion.argumentos),
        "strict": True,
    }


def esquemas_openai(definiciones: list[DefinicionHerramienta]) -> list[dict[str, Any]]:
    return [esquema_openai(d) for d in definiciones]

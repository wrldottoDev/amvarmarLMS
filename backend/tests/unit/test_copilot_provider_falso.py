"""`ProveedorFalsoDeterministico` y el candado de `Settings` que lo protege
(ADR-0012, Fase 3): sin esto, `COPILOT_PROVEEDOR_FALSO=true` olvidado en un
despliegue real dejaría a AMVI respondiendo con datos falsos en producción."""

import json

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.modules.copilot.provider_falso import ProveedorFalsoDeterministico

pytestmark = pytest.mark.unit

_HERRAMIENTAS = [{"type": "function", "name": "buscar_cargas"}]


def _mensaje_usuario(texto: str) -> dict:
    return {"role": "user", "content": texto}


def _resultado_de_herramienta(resultado: dict) -> dict:
    return {"type": "function_call_output", "call_id": "call_1", "output": json.dumps(resultado)}


class TestSettings:
    def test_proveedor_falso_fuera_de_local_no_arranca(self) -> None:
        with pytest.raises(ValidationError, match="ENVIRONMENT=local"):
            Settings(environment="production", copilot_proveedor_falso=True)

    def test_proveedor_falso_en_local_habilita_el_asistente_sin_clave(self) -> None:
        settings = Settings(environment="local", copilot_proveedor_falso=True, openai_api_key=None)
        assert settings.copilot_habilitado is True

    def test_sin_proveedor_falso_ni_clave_el_asistente_no_esta_habilitado(self) -> None:
        settings = Settings(environment="local", copilot_proveedor_falso=False, openai_api_key=None)
        assert settings.copilot_habilitado is False


class TestProveedorFalso:
    async def test_sin_codigo_de_carga_responde_texto_fijo_sin_llamar_herramientas(self) -> None:
        proveedor = ProveedorFalsoDeterministico()
        respuesta = await proveedor.responder(
            entrada=[_mensaje_usuario("hola, ¿cómo estás?")], herramientas=_HERRAMIENTAS
        )

        assert respuesta.llamadas_herramientas == ()
        assert respuesta.texto is not None
        assert "SHP-" in respuesta.texto

    async def test_con_codigo_de_carga_llama_a_buscar_cargas(self) -> None:
        proveedor = ProveedorFalsoDeterministico()
        respuesta = await proveedor.responder(
            entrada=[_mensaje_usuario("¿cómo está la carga SHP-2026-000123?")],
            herramientas=_HERRAMIENTAS,
        )

        assert respuesta.texto is None
        assert len(respuesta.llamadas_herramientas) == 1
        llamada = respuesta.llamadas_herramientas[0]
        assert llamada.nombre == "buscar_cargas"
        assert json.loads(llamada.argumentos_json) == {"q": "SHP-2026-000123"}

    async def test_no_llama_una_herramienta_que_no_se_le_ofrecio(self) -> None:
        """Igual que un modelo real debería comportarse: si `buscar_cargas` no
        está en la lista ofrecida, no la inventa."""
        proveedor = ProveedorFalsoDeterministico()
        respuesta = await proveedor.responder(
            entrada=[_mensaje_usuario("¿cómo está la carga SHP-2026-000123?")], herramientas=[]
        )

        assert respuesta.llamadas_herramientas == ()

    async def test_con_resultado_de_herramienta_cierra_en_texto_con_el_estado(self) -> None:
        proveedor = ProveedorFalsoDeterministico()
        entrada = [
            _mensaje_usuario("¿cómo está la carga SHP-2026-000123?"),
            _resultado_de_herramienta(
                {
                    "resultados": [{"shipment_number": "SHP-2026-000123", "estado": "STORED"}],
                    "hay_mas": False,
                }
            ),
        ]

        respuesta = await proveedor.responder(entrada=entrada, herramientas=_HERRAMIENTAS)

        assert respuesta.llamadas_herramientas == ()
        assert respuesta.texto == "La carga SHP-2026-000123 está en estado STORED."

    async def test_con_resultado_vacio_dice_que_no_encontro_nada(self) -> None:
        proveedor = ProveedorFalsoDeterministico()
        entrada = [
            _mensaje_usuario("¿cómo está la carga SHP-2026-999999?"),
            _resultado_de_herramienta({"resultados": [], "hay_mas": False}),
        ]

        respuesta = await proveedor.responder(entrada=entrada, herramientas=_HERRAMIENTAS)

        assert respuesta.texto == "No encontré ninguna carga con ese código."

    async def test_con_error_de_la_herramienta_lo_dice_en_vez_de_inventar(self) -> None:
        proveedor = ProveedorFalsoDeterministico()
        entrada = [
            _mensaje_usuario("¿cómo está la carga SHP-2026-000123?"),
            _resultado_de_herramienta({"error": "No tenés permiso para esa acción."}),
        ]

        respuesta = await proveedor.responder(entrada=entrada, herramientas=_HERRAMIENTAS)

        assert respuesta.texto is not None
        assert "No tenés permiso" in respuesta.texto

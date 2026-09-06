"""Smoke test contra la API real de OpenAI (ADR-0012).

Opt-in a propósito: `pytest -m smoke tests/integration/test_copilot_smoke.py`.
La suite normal (`pytest -q`) los excluye vía `addopts` en `pyproject.toml`.

Existe para no depender solo de memoria de una sesión de exploración manual:
si el proveedor cambia qué acepta `strict`, o el modelo configurado deja de
soportar `reasoning.effort`, esto lo detecta antes de un despliegue.
"""

import pytest

from app.core.config import get_settings
from app.modules.copilot.esquemas_openai import esquemas_openai
from app.modules.copilot.provider import ProveedorOpenAI
from app.modules.copilot.tools import HERRAMIENTAS

pytestmark = [pytest.mark.integration, pytest.mark.smoke]


def _requiere_clave() -> None:
    if not get_settings().openai_api_key:
        pytest.skip("OPENAI_API_KEY no configurada; smoke test omitido.")


class TestContratoDelProveedorReal:
    async def test_el_modelo_configurado_responde(self) -> None:
        _requiere_clave()
        proveedor = ProveedorOpenAI()

        respuesta = await proveedor.responder(
            entrada=[{"role": "user", "content": "Respondé solo con la palabra: ok"}],
            herramientas=[],
        )

        assert respuesta.texto is not None
        assert "ok" in respuesta.texto.lower()

    async def test_cada_herramienta_del_catalogo_tiene_un_esquema_que_la_api_acepta(
        self,
    ) -> None:
        """El regression test real de C1/C2 (ADR-0012): si algún día alguien
        vuelve a poner `default`, `title` o el envoltorio de Chat Completions,
        esto falla contra la API de verdad, no solo contra una suposición."""
        _requiere_clave()
        proveedor = ProveedorOpenAI()
        esquemas = esquemas_openai(list(HERRAMIENTAS.values()))

        for esquema in esquemas:
            respuesta = await proveedor.responder(
                entrada=[
                    {"role": "user", "content": "No hace falta que uses ninguna herramienta."}
                ],
                herramientas=[esquema],
            )
            assert respuesta is not None, f"Esquema rechazado: {esquema['name']}"

    async def test_una_llamada_a_herramienta_se_resuelve_sin_previous_response_id(
        self,
    ) -> None:
        """El regression test real del hallazgo de `store=false`: si OpenAI
        alguna vez soporta encadenar respuestas no almacenadas, o si deja de
        aceptar el patrón de reenviar los ítems de salida completos, esto lo
        detecta."""
        _requiere_clave()
        proveedor = ProveedorOpenAI()
        herramienta = esquemas_openai([HERRAMIENTAS["consultar_estado_carga"]])[0]

        entrada = [{"role": "user", "content": "¿Cuál es el estado de SHP-2026-000001?"}]
        primera = await proveedor.responder(entrada=entrada, herramientas=[herramienta])
        assert primera.llamadas_herramientas, "El modelo no pidió la herramienta esperada."

        llamada = primera.llamadas_herramientas[0]
        entrada = [
            *entrada,
            *primera.items_salida,
            {
                "type": "function_call_output",
                "call_id": llamada.call_id,
                "output": '{"status": "EN_TRANSITO"}',
            },
        ]
        segunda = await proveedor.responder(entrada=entrada, herramientas=[herramienta])

        assert segunda.texto is not None
        assert not segunda.llamadas_herramientas

    async def test_temperature_sigue_rechazada_por_el_modelo_configurado(self) -> None:
        """Si esto empieza a pasar, `provider.py` puede volver a usar
        `temperature` en vez de `reasoning.effort` — el ADR-0012 documenta por
        qué hoy no se envía."""
        _requiere_clave()
        from openai import BadRequestError, OpenAI

        settings = get_settings()
        cliente = OpenAI(api_key=settings.openai_api_key)

        with pytest.raises(BadRequestError):
            cliente.responses.create(
                model=settings.copilot_model,
                input="di ok",
                temperature=0.2,
                max_output_tokens=20,
            )

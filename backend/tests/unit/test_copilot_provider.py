"""Los ítems de salida de OpenAI se reenvían en la vuelta siguiente del turno
(`store=false`): tienen que ir con los nombres que la API acepta en `input`."""

from openai.types.responses import ResponseFunctionToolCall, ResponseReasoningItem

from app.modules.copilot.provider import item_para_reenviar


def test_function_call_se_reenvia_con_los_nombres_de_la_api() -> None:
    item = ResponseFunctionToolCall.model_validate(
        {
            "type": "function_call",
            "id": "fc_1",
            "call_id": "call_1",
            "name": "hora",
            "arguments": "{}",
            "status": "completed",
            "async": False,
        }
    )

    datos = item_para_reenviar(item)

    assert "async_" not in datos
    assert "status" not in datos
    assert datos["call_id"] == "call_1"
    assert datos["type"] == "function_call"


def test_razonamiento_conserva_el_contenido_cifrado_y_quita_los_nulos() -> None:
    item = ResponseReasoningItem.model_validate(
        {
            "type": "reasoning",
            "id": "rs_1",
            "summary": [],
            "encrypted_content": "cifrado",
            "status": None,
        }
    )

    datos = item_para_reenviar(item)

    assert datos["encrypted_content"] == "cifrado"
    assert "status" not in datos
    assert all(valor is not None for valor in datos.values())

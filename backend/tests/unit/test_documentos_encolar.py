"""Regresión: si el broker rechaza la tarea, el 503 deja la causa en el log.

Antes solo quedaba la línea de acceso con el 503 y había que adivinar si era
Redis, el disco o un import.
"""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from structlog.testing import capture_logs

from app.core.errors import ErrorDeAplicacion
from app.modules.documents import router


async def test_un_broker_caido_queda_en_el_log_con_su_traza(monkeypatch) -> None:
    def broker_caido(_document_id):
        raise ConnectionError("Error 111 connecting to redis:6379. Connection refused.")

    monkeypatch.setattr(router, "_encolar_documento", broker_caido)
    document_id = uuid4()

    with capture_logs() as logs, pytest.raises(ErrorDeAplicacion) as error:
        await router._confirmar_y_encolar(AsyncMock(), document_id=document_id)

    assert error.value.status_code == 503
    [registro] = [r for r in logs if r["event"] == "documento_no_encolado"]
    assert registro["document_id"] == str(document_id)
    assert registro["exc_info"] is True

"""Fixtures compartidas por las pruebas del asistente (router e historial)."""

import pytest

from app.main import app
from app.modules.copilot import provider as copilot_provider
from app.modules.copilot.router import _fabrica_proveedor


@pytest.fixture
def usar_proveedor():
    """Reemplaza el proveedor real por uno falso solo para el test, y limpia
    al terminar aunque el test falle."""

    def _usar(proveedor) -> None:
        app.dependency_overrides[_fabrica_proveedor] = lambda: lambda: proveedor

    yield _usar
    app.dependency_overrides.pop(_fabrica_proveedor, None)


@pytest.fixture(autouse=True)
def _breaker_limpio():
    """El circuit breaker es estado de módulo — sin esto, un test que lo abre
    dejaría el siguiente empezando ya degradado."""
    copilot_provider._reiniciar_breaker_para_pruebas()
    yield
    copilot_provider._reiniciar_breaker_para_pruebas()

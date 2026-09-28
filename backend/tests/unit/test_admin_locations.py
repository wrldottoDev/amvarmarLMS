import pytest
from pydantic import ValidationError

from app.modules.admin.router import CrearUbicacionRequest


@pytest.mark.parametrize(
    ("campo", "valor"),
    [
        ("country_code", "cn"),
        ("country_code", "CHN"),
        ("city_code", "s"),
        ("city_code", "sha"),
        ("location_code", "cn-sha"),
        ("location_code", "CNSHA"),
    ],
)
def test_rechaza_codigos_de_ubicacion_invalidos(campo: str, valor: str) -> None:
    datos = {
        "country_code": "CN",
        "city_code": "SHA",
        "location_code": "CN-SHA",
        "name": "Shanghai",
        campo: valor,
    }
    with pytest.raises(ValidationError):
        CrearUbicacionRequest(**datos)


def test_acepta_shanghai_y_panama() -> None:
    assert (
        CrearUbicacionRequest(
            country_code="CN", city_code="SHA", location_code="CN-SHA", name="Shanghai"
        ).location_code
        == "CN-SHA"
    )
    assert (
        CrearUbicacionRequest(
            country_code="PA", city_code="PTY", location_code="PA-PTY", name="Panamá"
        ).location_code
        == "PA-PTY"
    )

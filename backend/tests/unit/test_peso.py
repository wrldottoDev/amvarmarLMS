from decimal import Decimal

import pytest

from app.modules.shipments.peso import PesoInvalido, convertir_peso


def test_convierte_kilos_a_libras_con_tres_decimales() -> None:
    peso = convertir_peso(Decimal("10"), "KG")

    assert peso.kg == Decimal("10.000")
    assert peso.lb == Decimal("22.046")
    assert peso.source_unit == "KG"


def test_convierte_libras_a_kilos_con_tres_decimales() -> None:
    peso = convertir_peso(Decimal("120"), "LB")

    assert peso.kg == Decimal("54.431")
    assert peso.lb == Decimal("120.000")
    assert peso.source_unit == "LB"


@pytest.mark.parametrize("valor", [Decimal("0"), Decimal("-1"), Decimal("NaN")])
def test_rechaza_pesos_no_positivos_o_no_finitos(valor: Decimal) -> None:
    with pytest.raises(PesoInvalido):
        convertir_peso(valor, "KG")

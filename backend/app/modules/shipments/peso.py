"""Conversión canónica del peso físico de una carga."""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from app.core.errors import ReglaDeNegocioViolada


class UnidadPeso(StrEnum):
    KG = "KG"
    LB = "LB"


class PesoInvalido(ReglaDeNegocioViolada):
    code = "PESO_INVALIDO"


KG_A_LB = Decimal("2.2046226218487757")
TRES_DECIMALES = Decimal("0.001")


@dataclass(frozen=True)
class PesoConvertido:
    kg: Decimal
    lb: Decimal
    source_unit: UnidadPeso


def convertir_peso(value: Decimal, unit: UnidadPeso | str) -> PesoConvertido:
    """Calcula kg y lb desde una única fuente con redondeo uniforme."""
    try:
        valor = Decimal(value)
        unidad = UnidadPeso(unit)
    except (ArithmeticError, ValueError) as error:
        raise PesoInvalido("Indique un peso y una unidad válidos.") from error

    if not valor.is_finite() or valor <= 0:
        raise PesoInvalido("El peso debe ser mayor que cero.")

    if unidad == UnidadPeso.KG:
        kg = valor
        lb = valor * KG_A_LB
    else:
        kg = valor / KG_A_LB
        lb = valor

    return PesoConvertido(
        kg=kg.quantize(TRES_DECIMALES, rounding=ROUND_HALF_UP),
        lb=lb.quantize(TRES_DECIMALES, rounding=ROUND_HALF_UP),
        source_unit=unidad,
    )

"""Motor de cálculo de impuestos de importación (estimador, fase F1).

Determinista, sin base de datos ni IA: recibe valores y tasas y devuelve el
desglose en cascada del instructivo de la DGA MH-DGA-PRO09-INS-004 (ago-2023),
https://www.hacienda.go.cr/docs/INSTRUCTIVO_CALCULO_OBLIGACION_TRIBUTARIA.pdf

Alcance de F1: importación definitiva con DAI, Selectivo de Consumo, Ley 6946,
margen G/E, IVA y otros cargos de la declaración ya expresados en colones
(Caldera, timbres). Fuera de alcance: preferencias por tratado (sustituyen a
DAI + 6946 con otra base, F7), INDER, IFAM e impuestos específicos (alcohol,
tabaco, combustibles: excluidos del MVP).

Los casos dorados están en `tests/unit/test_motor_aranceles.py`; el contexto,
en `docs/estimador-impuestos/01_INVESTIGACION_ADUANERA.md`.
"""

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from app.core.errors import ReglaDeNegocioViolada

# Sube con cada cambio de fórmula: una estimación guardada dice con qué
# versión se calculó.
REGLAS_VERSION = "2026.10-1"

CENTIMOS = Decimal("0.01")
CIEN = Decimal("100")


class DatosDeEstimacionInvalidos(ReglaDeNegocioViolada):
    code = "ESTIMACION_DATOS_INVALIDOS"


@dataclass(frozen=True)
class Politica:
    """Lo que el instructivo no deja cerrado (docs 01 §9, decisión D15).

    Explícito y con nombre para poder contrastarlo con DUAs reales.
    """

    # Fórmula general (p. 7): SC = (VAxTC + DAI) x %SC. El ejemplo del
    # vehículo (p. 12-13) suma también la Ley 6946 a esa base.
    sc_incluye_ley_6946: bool = False
    # Cada monto se lleva a céntimos y el siguiente se calcula sobre el ya
    # redondeado (ejemplo del whisky, p. 8). El modo HALF_UP es un supuesto:
    # ningún ejemplo oficial cae en medio céntimo.
    redondeo: str = ROUND_HALF_UP


@dataclass(frozen=True)
class Tasas:
    """Porcentajes del inciso: `Decimal("14")` es 14 %."""

    dai: Decimal
    ley_6946: Decimal = Decimal("1")
    selectivo: Decimal = Decimal("0")
    margen_ge: Decimal = Decimal("0")
    iva: Decimal = Decimal("13")


@dataclass(frozen=True)
class Entrada:
    """El valor aduanero es CIF: mercancía + flete + seguro hasta el primer
    puerto centroamericano (Reglamento CA de Valoración, art. 4)."""

    valor_mercancia_usd: Decimal
    flete_usd: Decimal
    seguro_usd: Decimal
    # Colones por dólar: referencia de venta del BCCR del día de aceptación.
    tipo_cambio: Decimal
    tasas: Tasas
    # Ya en colones. Integran la base del IVA y el total (p. 18-19, p. 24).
    otros_cargos_crc: Decimal = Decimal("0")
    politica: Politica = field(default_factory=Politica)


@dataclass(frozen=True)
class Linea:
    concepto: str
    base: Decimal
    tasa: Decimal | None
    monto: Decimal
    # El margen G/E no es un impuesto: solo agranda la base del IVA (p. 12, 24).
    suma_al_total: bool
    fuente: str


@dataclass(frozen=True)
class Desglose:
    valor_aduanero_usd: Decimal
    valor_aduanero_crc: Decimal
    lineas: tuple[Linea, ...]
    total: Decimal
    reglas_version: str
    politica: Politica

    def monto(self, concepto: str) -> Decimal:
        return next(linea.monto for linea in self.lineas if linea.concepto == concepto)


def calcular(entrada: Entrada) -> Desglose:
    _validar(entrada)
    tasas = entrada.tasas

    def redondear(valor: Decimal) -> Decimal:
        return valor.quantize(CENTIMOS, rounding=entrada.politica.redondeo)

    def aplicar(base: Decimal, porcentaje: Decimal) -> Decimal:
        return redondear(base * porcentaje / CIEN)

    va_usd = entrada.valor_mercancia_usd + entrada.flete_usd + entrada.seguro_usd
    va_crc = redondear(va_usd * entrada.tipo_cambio)

    dai = aplicar(va_crc, tasas.dai)
    ley_6946 = aplicar(va_crc, tasas.ley_6946)

    base_sc = va_crc + dai + (ley_6946 if entrada.politica.sc_incluye_ley_6946 else Decimal("0"))
    selectivo = aplicar(base_sc, tasas.selectivo)

    base_ge = va_crc + dai + selectivo + ley_6946
    margen_ge = aplicar(base_ge, tasas.margen_ge)

    base_iva = base_ge + margen_ge + entrada.otros_cargos_crc
    iva = aplicar(base_iva, tasas.iva)

    lineas = [
        Linea("DAI", va_crc, tasas.dai, dai, True, "Instructivo DGA p. 6"),
        Linea("Ley 6946", va_crc, tasas.ley_6946, ley_6946, True, "Instructivo DGA p. 7"),
    ]
    if tasas.selectivo:
        lineas.append(
            Linea(
                "Selectivo de Consumo",
                base_sc,
                tasas.selectivo,
                selectivo,
                True,
                "Instructivo DGA p. 7; Ley 4961",
            )
        )
    if tasas.margen_ge:
        lineas.append(
            Linea("Margen G/E", base_ge, tasas.margen_ge, margen_ge, False, "Instructivo DGA p. 12")
        )
    if entrada.otros_cargos_crc:
        lineas.append(
            Linea(
                "Otros cargos de la declaración",
                entrada.otros_cargos_crc,
                None,
                entrada.otros_cargos_crc,
                True,
                "Instructivo DGA p. 18-19",
            )
        )
    lineas.append(Linea("IVA", base_iva, tasas.iva, iva, True, "Instructivo DGA p. 18; Ley 9635"))

    return Desglose(
        valor_aduanero_usd=va_usd,
        valor_aduanero_crc=va_crc,
        lineas=tuple(lineas),
        total=sum((linea.monto for linea in lineas if linea.suma_al_total), Decimal("0")),
        reglas_version=REGLAS_VERSION,
        politica=entrada.politica,
    )


def _validar(entrada: Entrada) -> None:
    montos = {
        "valor de la mercancía": entrada.valor_mercancia_usd,
        "flete": entrada.flete_usd,
        "seguro": entrada.seguro_usd,
        "tipo de cambio": entrada.tipo_cambio,
        "otros cargos": entrada.otros_cargos_crc,
        **{f"tasa {nombre}": valor for nombre, valor in vars(entrada.tasas).items()},
    }
    for nombre, valor in montos.items():
        if not isinstance(valor, Decimal) or not valor.is_finite() or valor < 0:
            raise DatosDeEstimacionInvalidos(f"El {nombre} debe ser un número decimal no negativo.")
    if entrada.valor_mercancia_usd <= 0 or entrada.tipo_cambio <= 0:
        raise DatosDeEstimacionInvalidos(
            "El valor de la mercancía y el tipo de cambio deben ser mayores que cero."
        )

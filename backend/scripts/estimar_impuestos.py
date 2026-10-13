"""Prueba el motor tributario con los datos de un DUA real (estimador, fase F1).

Las tasas se pasan a mano, tal como figuran en el DUA o en TICA. Decimales con
punto. Ejemplo oficial del instructivo DGA (p. 18), total ¢1.730.870,40:

    python -m scripts.estimar_impuestos --valor 9600 --tc 602 --dai 14

Con flete, seguro, Selectivo y cargos de la declaración:

    python -m scripts.estimar_impuestos --valor 9000 --flete 450 --seguro 150 \\
        --tc 602 --dai 14 --sc 10 --otros-cargos 146.48

Si el total no coincide con el DUA, probar `--sc-incluye-6946` (la duda
abierta de la base del Selectivo, docs/estimador-impuestos/01 §9).
"""

import argparse
import sys
from decimal import Decimal, InvalidOperation

from app.modules.aranceles.motor import (
    DatosDeEstimacionInvalidos,
    Desglose,
    Entrada,
    Politica,
    Tasas,
    calcular,
)


def _decimal(texto: str) -> Decimal:
    try:
        return Decimal(texto)
    except InvalidOperation as error:
        raise argparse.ArgumentTypeError(
            f"«{texto}» no es un número (usar punto decimal)."
        ) from error


def _colones(valor: Decimal) -> str:
    # Formato de Costa Rica: punto de miles, coma decimal.
    return "¢" + f"{valor:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def _dolares(valor: Decimal) -> str:
    return "US$" + _colones(valor)[1:]


def formatear(desglose: Desglose, entrada: Entrada) -> str:
    lineas = [
        f"Estimación de prueba (reglas {desglose.reglas_version}). No es una liquidación oficial.",
        "",
        f"Valor aduanero: {_dolares(desglose.valor_aduanero_usd)}"
        f" = mercancía {_dolares(entrada.valor_mercancia_usd)}"
        f" + flete {_dolares(entrada.flete_usd)} + seguro {_dolares(entrada.seguro_usd)}",
        f"                {_colones(desglose.valor_aduanero_crc)}"
        f" al tipo de cambio {_colones(entrada.tipo_cambio)[1:]}",
        "",
        f"{'Concepto':<32}{'Base':>18}{'Tasa':>9}{'Monto':>18}",
    ]
    for linea in desglose.lineas:
        tasa = f"{linea.tasa.normalize():f} %" if linea.tasa is not None else ""
        nota = "" if linea.suma_al_total else "  (no suma: solo base del IVA)"
        lineas.append(
            f"{linea.concepto:<32}{_colones(linea.base):>18}{tasa:>9}{_colones(linea.monto):>18}{nota}"
        )
    politica = desglose.politica
    lineas += [
        "",
        f"{'Total de impuestos':<59}{_colones(desglose.total):>18}",
        "",
        "Política: base del Selectivo "
        + ("con" if politica.sc_incluye_ley_6946 else "sin")
        + f" Ley 6946; cada monto redondeado a céntimos ({politica.redondeo}).",
    ]
    return "\n".join(lineas)


def principal(argumentos: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Estima los impuestos de importación con tasas puestas a mano."
    )
    parser.add_argument("--valor", type=_decimal, required=True, help="Valor de la mercancía, US$.")
    parser.add_argument("--flete", type=_decimal, default=Decimal("0"), help="Flete, US$.")
    parser.add_argument("--seguro", type=_decimal, default=Decimal("0"), help="Seguro, US$.")
    parser.add_argument(
        "--tc", type=_decimal, required=True, help="Tipo de cambio, colones por dólar."
    )
    parser.add_argument("--dai", type=_decimal, required=True, help="DAI, %%.")
    parser.add_argument("--ley-6946", type=_decimal, default=Decimal("1"), help="Ley 6946, %% (1).")
    parser.add_argument(
        "--sc", type=_decimal, default=Decimal("0"), help="Selectivo de Consumo, %%."
    )
    parser.add_argument("--ge", type=_decimal, default=Decimal("0"), help="Margen G/E, %%.")
    parser.add_argument("--iva", type=_decimal, default=Decimal("13"), help="IVA, %% (13).")
    parser.add_argument(
        "--otros-cargos",
        type=_decimal,
        default=Decimal("0"),
        help="Cargos de la declaración ya en colones (Caldera, timbres).",
    )
    parser.add_argument(
        "--sc-incluye-6946",
        action="store_true",
        help="Suma la Ley 6946 a la base del Selectivo (ejemplo del vehículo del instructivo).",
    )
    args = parser.parse_args(argumentos)

    entrada = Entrada(
        valor_mercancia_usd=args.valor,
        flete_usd=args.flete,
        seguro_usd=args.seguro,
        tipo_cambio=args.tc,
        tasas=Tasas(
            dai=args.dai,
            ley_6946=args.ley_6946,
            selectivo=args.sc,
            margen_ge=args.ge,
            iva=args.iva,
        ),
        otros_cargos_crc=args.otros_cargos,
        politica=Politica(sc_incluye_ley_6946=args.sc_incluye_6946),
    )
    try:
        desglose = calcular(entrada)
    except DatosDeEstimacionInvalidos as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    print(formatear(desglose, entrada))
    return 0


if __name__ == "__main__":
    sys.exit(principal())

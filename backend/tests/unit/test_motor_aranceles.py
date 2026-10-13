"""Casos dorados del motor tributario.

Cada caso es un ejemplo impreso en el instructivo de la DGA
MH-DGA-PRO09-INS-004 (ago-2023), con los montos tal como aparecen. Cuando el
instructivo tiene un error interno, el caso lo dice y se usa lo que el
ejemplo realmente aplicó.
"""

from decimal import Decimal
from pathlib import Path

import pytest
from scripts import estimar_impuestos

from app.modules.aranceles import motor
from app.modules.aranceles.motor import Entrada, Politica, Tasas, calcular


def _entrada(valor: str, tasas: Tasas, **extra: object) -> Entrada:
    return Entrada(
        valor_mercancia_usd=Decimal(valor),
        flete_usd=Decimal("0"),
        seguro_usd=Decimal("0"),
        tipo_cambio=Decimal("602.00"),
        tasas=tasas,
        **extra,  # type: ignore[arg-type]
    )


class TestCasosDoradosDelInstructivo:
    def test_p18_pantalones_dai_ley_6946_e_iva(self) -> None:
        desglose = calcular(_entrada("9600", Tasas(dai=Decimal("14"))))

        assert desglose.valor_aduanero_crc == Decimal("5779200.00")
        assert desglose.monto("DAI") == Decimal("809088.00")  # p. 6 y 18
        assert desglose.monto("Ley 6946") == Decimal("57792.00")  # p. 7 y 18
        assert desglose.monto("IVA") == Decimal("863990.40")  # p. 18
        assert desglose.total == Decimal("1730870.40")

    def test_p7_selectivo_sobre_valor_mas_dai(self) -> None:
        desglose = calcular(_entrada("9600", Tasas(dai=Decimal("14"), selectivo=Decimal("15"))))

        assert desglose.monto("Selectivo de Consumo") == Decimal("988243.20")

    def test_p8_cada_monto_se_redondea_y_el_siguiente_usa_el_redondeado(self) -> None:
        desglose = calcular(_entrada("5453.54", Tasas(dai=Decimal("14"), selectivo=Decimal("10"))))

        assert desglose.valor_aduanero_crc == Decimal("3283031.08")
        assert desglose.monto("DAI") == Decimal("459624.35")
        assert desglose.monto("Selectivo de Consumo") == Decimal("374265.54")
        assert desglose.monto("Ley 6946") == Decimal("32830.31")

    def test_p19_otros_cargos_integran_la_base_del_iva(self) -> None:
        # Caldera, 400 kg: ¢96,32 + ¢48,96 + ¢0,40 + ¢0,80.
        desglose = calcular(
            _entrada("9600", Tasas(dai=Decimal("14")), otros_cargos_crc=Decimal("146.48"))
        )

        assert desglose.monto("IVA") == Decimal("864009.44")

    def test_p25_cascada_completa_con_selectivo(self) -> None:
        # El enunciado dice SC 15 %, pero el ejemplo aplica 10 %: se usa lo aplicado.
        desglose = calcular(_entrada("9600", Tasas(dai=Decimal("14"), selectivo=Decimal("10"))))

        assert desglose.monto("Selectivo de Consumo") == Decimal("658828.80")
        assert desglose.monto("IVA") == Decimal("949638.14")
        assert desglose.total == Decimal("2475346.94")

    def test_p23_preferencia_cero_es_dai_y_ley_6946_en_cero(self) -> None:
        # Panamá 0 %. Con tasa preferencial distinta de cero la base del SC
        # cambia (incluye la preferencia completa): eso es F7, no está acá.
        desglose = calcular(
            _entrada("720", Tasas(dai=Decimal("0"), ley_6946=Decimal("0"), selectivo=Decimal("15")))
        )

        assert desglose.monto("Selectivo de Consumo") == Decimal("65016.00")
        assert desglose.monto("IVA") == Decimal("64799.28")
        assert desglose.total == Decimal("129815.28")

    def test_p12_vehiculo_con_margen_ge_que_no_suma_al_total(self) -> None:
        # Este ejemplo suma la Ley 6946 a la base del SC (la fórmula general
        # de la p. 7 no): es la duda abierta D15.
        desglose = calcular(
            _entrada(
                "7000",
                Tasas(dai=Decimal("0"), selectivo=Decimal("48"), margen_ge=Decimal("25")),
                politica=Politica(sc_incluye_ley_6946=True),
            )
        )

        assert desglose.monto("Ley 6946") == Decimal("42140.00")
        assert desglose.monto("Selectivo de Consumo") == Decimal("2042947.20")
        assert desglose.monto("Margen G/E") == Decimal("1574771.80")
        assert desglose.monto("IVA") == Decimal("1023601.67")
        assert desglose.total == Decimal("3108688.87")

    def test_p22_el_instructivo_imprime_un_iva_equivocado(self) -> None:
        """Guatemala 0 % con SC 15 %: el instructivo imprime IVA ¢863.860,40 y
        total ¢1.730.740,40, pero (5.779.200 + 866.880) x 13 % = 863.990,40."""
        desglose = calcular(
            _entrada(
                "9600", Tasas(dai=Decimal("0"), ley_6946=Decimal("0"), selectivo=Decimal("15"))
            )
        )

        assert desglose.monto("Selectivo de Consumo") == Decimal("866880.00")
        assert desglose.monto("IVA") == Decimal("863990.40")


class TestReglas:
    def test_el_valor_aduanero_es_cif(self) -> None:
        entrada = Entrada(
            valor_mercancia_usd=Decimal("9000"),
            flete_usd=Decimal("450"),
            seguro_usd=Decimal("150"),
            tipo_cambio=Decimal("602.00"),
            tasas=Tasas(dai=Decimal("14")),
        )

        assert calcular(entrada).valor_aduanero_crc == Decimal("5779200.00")

    def test_sin_tasas_solo_quedan_los_otros_cargos(self) -> None:
        tasas = Tasas(dai=Decimal("0"), ley_6946=Decimal("0"), iva=Decimal("0"))

        desglose = calcular(_entrada("1000", tasas, otros_cargos_crc=Decimal("70")))

        assert desglose.total == Decimal("70")

    def test_la_cascada_es_monotona_en_el_valor(self) -> None:
        tasas = Tasas(dai=Decimal("14"), selectivo=Decimal("10"), margen_ge=Decimal("25"))

        totales = [calcular(_entrada(valor, tasas)).total for valor in ("100", "100.01", "5000")]

        assert totales == sorted(totales)

    def test_la_estimacion_dice_con_que_reglas_se_calculo(self) -> None:
        desglose = calcular(_entrada("9600", Tasas(dai=Decimal("14"))))

        assert desglose.reglas_version == motor.REGLAS_VERSION
        assert desglose.politica == Politica()

    @pytest.mark.parametrize(
        "cambio",
        [
            {"valor_mercancia_usd": Decimal("0")},
            {"tipo_cambio": Decimal("0")},
            {"flete_usd": Decimal("-1")},
            {"seguro_usd": Decimal("NaN")},
            {"otros_cargos_crc": Decimal("Infinity")},
            {"tasas": Tasas(dai=Decimal("-14"))},
        ],
    )
    def test_rechaza_datos_invalidos(self, cambio: dict[str, object]) -> None:
        datos: dict[str, object] = {
            "valor_mercancia_usd": Decimal("100"),
            "flete_usd": Decimal("0"),
            "seguro_usd": Decimal("0"),
            "tipo_cambio": Decimal("602"),
            "tasas": Tasas(dai=Decimal("14")),
        } | cambio

        with pytest.raises(motor.DatosDeEstimacionInvalidos):
            calcular(Entrada(**datos))  # type: ignore[arg-type]

    def test_el_motor_no_usa_float(self) -> None:
        """Un `float` en el cálculo de dinero rompe los céntimos sin avisar."""
        assert "float" not in Path(motor.__file__).read_text(encoding="utf-8")


class TestScriptDePrueba:
    def test_imprime_el_desglose_del_ejemplo_oficial(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        codigo = estimar_impuestos.principal(["--valor", "9600", "--tc", "602", "--dai", "14"])

        salida = capsys.readouterr().out
        assert codigo == 0
        assert "¢5.779.200,00" in salida
        assert "¢863.990,40" in salida
        assert "¢1.730.870,40" in salida

    def test_datos_invalidos_terminan_con_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        codigo = estimar_impuestos.principal(["--valor", "0", "--tc", "602", "--dai", "14"])

        assert codigo == 2
        assert "mayores que cero" in capsys.readouterr().err

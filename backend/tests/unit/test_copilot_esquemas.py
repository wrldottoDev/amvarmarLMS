"""Esquemas del catálogo de herramientas (ADR-0012, enmienda 2026-09).

El regression test real (contra la API viva) está en
`tests/integration/test_copilot_smoke.py`, marcado `smoke` y opt-in. Estas
pruebas verifican la LÓGICA del generador sin red: que efectivamente produce
la forma que `strict` exige, sin depender de que el proveedor siga
disponible para correr la suite normal.
"""

import pytest
from pydantic import BaseModel

from app.modules.copilot.esquemas_openai import EsquemaNoSoportado, esquema_openai, esquemas_openai
from app.modules.copilot.tools import (
    HERRAMIENTAS,
    ClaseHerramienta,
    DefinicionHerramienta,
    Requiere,
)
from app.modules.rbac.catalog import Perm


class TestFormaPlana:
    """Reemplaza el envoltorio de Chat Completions que tenía
    `esquema_para_proveedor()` — este es el que exige la Responses API."""

    def test_las_claves_van_al_mismo_nivel_no_anidadas_bajo_function(self) -> None:
        esquema = esquema_openai(HERRAMIENTAS["consultar_estado_carga"])

        assert esquema["type"] == "function"
        assert "name" in esquema
        assert "parameters" in esquema
        assert esquema["strict"] is True
        assert "function" not in esquema


class TestCumpleStrict:
    @pytest.mark.parametrize("nombre", list(HERRAMIENTAS))
    def test_additional_properties_false(self, nombre: str) -> None:
        esquema = esquema_openai(HERRAMIENTAS[nombre])
        assert esquema["parameters"]["additionalProperties"] is False

    @pytest.mark.parametrize("nombre", list(HERRAMIENTAS))
    def test_todos_los_campos_estan_en_required(self, nombre: str) -> None:
        esquema = esquema_openai(HERRAMIENTAS[nombre])
        propiedades = set(esquema["parameters"]["properties"])
        requeridos = set(esquema["parameters"]["required"])
        assert propiedades == requeridos

    @pytest.mark.parametrize("nombre", list(HERRAMIENTAS))
    def test_nada_de_default_ni_title_ni_restricciones_numericas(self, nombre: str) -> None:
        """C2 (ADR-0012): estos keywords son exactamente los que hacían que
        `model_json_schema()` sin ajustar fuera rechazado por la API real."""
        esquema = esquema_openai(HERRAMIENTAS[nombre])
        for propiedad in esquema["parameters"]["properties"].values():
            assert "default" not in propiedad
            assert "title" not in propiedad
            assert "maxLength" not in propiedad
            assert "minimum" not in propiedad
            assert "maximum" not in propiedad

    def test_un_campo_opcional_de_pydantic_pasa_a_requerido_nullable(self) -> None:
        """`crear_prealerta_borrador` tiene 4 campos opcionales — el caso real
        que había que traducir para strict."""
        esquema = esquema_openai(HERRAMIENTAS["crear_prealerta_borrador"])
        propiedades = esquema["parameters"]["properties"]

        opcional = propiedades["peso_kg"]
        assert set(opcional["type"]) == {"number", "null"}
        assert "peso_kg" in esquema["parameters"]["required"]


class TestEsquemaNoSoportado:
    """El generador falla temprano y explícito ante formas que no cubre, en
    vez de emitir algo que la API rechace en producción sin avisar antes."""

    def test_objeto_anidado_lanza(self) -> None:
        class ConSubmodelo(BaseModel):
            interno: dict[str, str]

        definicion = DefinicionHerramienta(
            nombre="prueba_objeto_anidado",
            descripcion="prueba",
            argumentos=ConSubmodelo,
            autorizacion=Requiere(Perm.SHIPMENTS_READ),
            clase=ClaseHerramienta.LECTURA,
        )

        with pytest.raises(EsquemaNoSoportado):
            esquema_openai(definicion)

    def test_submodelo_con_defs_lanza(self) -> None:
        class Sub(BaseModel):
            valor: str

        class ConSub(BaseModel):
            sub: Sub

        definicion = DefinicionHerramienta(
            nombre="prueba_defs",
            descripcion="prueba",
            argumentos=ConSub,
            autorizacion=Requiere(Perm.SHIPMENTS_READ),
            clase=ClaseHerramienta.LECTURA,
        )

        with pytest.raises(EsquemaNoSoportado):
            esquema_openai(definicion)


class TestEsquemasOpenaiPlural:
    def test_genera_uno_por_definicion(self) -> None:
        definiciones = list(HERRAMIENTAS.values())
        esquemas = esquemas_openai(definiciones)
        assert len(esquemas) == len(definiciones)
        assert {e["name"] for e in esquemas} == set(HERRAMIENTAS)

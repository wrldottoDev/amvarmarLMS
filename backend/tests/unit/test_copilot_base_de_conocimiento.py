"""Base de conocimiento de `como_hago` (ADR-0012, Fase 3)."""

import pytest

from app.modules.copilot import base_de_conocimiento as bc

pytestmark = pytest.mark.unit


def test_carga_todos_los_md_menos_los_que_empiezan_con_guion_bajo() -> None:
    archivos = sorted(p.name for p in bc._DIRECTORIO.glob("*.md"))
    entradas = bc._entradas()

    assert any(a.startswith("_") for a in archivos), "el fixture de _pendientes.md debe existir"
    assert len(entradas) == len([a for a in archivos if not a.startswith("_")])
    assert all(e.titulo and e.cuerpo and e.palabras_clave for e in entradas)


class TestBuscar:
    def test_sin_palabras_en_comun_no_devuelve_nada(self) -> None:
        assert bc.buscar("algo que no existe en ningún lado") is None

    def test_encuentra_por_orden_de_palabras_distinto_al_de_la_frase_clave(self) -> None:
        # Palabra clave real: "nueva carga". La pregunta la trae al revés.
        entrada = bc.buscar("¿cómo hago una carga nueva?")
        assert entrada is not None
        assert "crear" in entrada.titulo.lower() or "creo" in entrada.titulo.lower()

    def test_ignora_signos_de_puntuacion_pegados_a_la_palabra(self) -> None:
        entrada = bc.buscar("¿cómo cancelo un despacho?")
        assert entrada is not None
        assert "despacho" in entrada.titulo.lower()

    def test_encuentra_documentos_pendientes(self) -> None:
        entrada = bc.buscar("me falta subir un documento")
        assert entrada is not None
        assert "documento" in entrada.titulo.lower()

    def test_encuentra_el_historial_de_una_carga(self) -> None:
        entrada = bc.buscar("¿dónde veo el historial de una carga?")
        assert entrada is not None
        assert "historial" in entrada.titulo.lower()

    def test_encuentra_como_editar_una_carga(self) -> None:
        entrada = bc.buscar("necesito corregir un dato de una carga")
        assert entrada is not None
        assert "edito" in entrada.titulo.lower() or "editar" in entrada.titulo.lower()

    def test_encuentra_alta_de_usuario_de_la_propia_empresa(self) -> None:
        entrada = bc.buscar("cómo agrego un usuario a mi empresa")
        assert entrada is not None
        assert "usuario" in entrada.titulo.lower()

    def test_es_determinista_ante_la_misma_pregunta(self) -> None:
        primera = bc.buscar("¿cómo veo mis avisos?")
        segunda = bc.buscar("¿cómo veo mis avisos?")
        assert primera == segunda

    def test_el_cuerpo_no_conserva_el_titulo_ni_el_comentario_de_palabras_clave(self) -> None:
        for entrada in bc._entradas():
            assert entrada.titulo not in entrada.cuerpo
            assert "palabras_clave" not in entrada.cuerpo
            assert "<!--" not in entrada.cuerpo

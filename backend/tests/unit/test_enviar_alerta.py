from scripts.enviar_alerta import componer


def test_la_alerta_lleva_prefijo_y_escapa_el_html() -> None:
    correo = componer("Disco al 91 %", "uso: 91 % <script>")

    assert correo.asunto == "[AMVARMAR LMS] Disco al 91 %"
    assert correo.texto == "uso: 91 % <script>"
    assert "<script>" not in correo.html
    assert "&lt;script&gt;" in correo.html

"""Scripts de limpieza de datos legacy (Paso 5.1).

Estos scripts van a correr contra la base de producción del sistema viejo.
Entregarlos sin ejecutarlos sería pedir que se prueben ahí, que es exactamente
lo que el paso quiere evitar.

La prueba levanta un esquema legacy mínimo que reproduce la FORMA de los
problemas del inventario del Paso 0.3 —correos vacíos, correos duplicados,
cargas sin cliente, WR con caracteres de captura— y corre los scripts reales,
sin copiarlos ni adaptarlos. Los datos de la semilla son inventados: los conteos
reales viven en `docs/migration/inventario.md`.

`psql` se ejecuta DENTRO del contenedor: la máquina de desarrollo tiene
herramientas de PostgreSQL 14 y el servidor es 16.
"""

import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.slow]

_RAIZ = Path(__file__).resolve().parents[3]
_CORRECCIONES = _RAIZ / "docs/migration/correcciones"
_FIXTURES = Path(__file__).parent


@pytest.fixture
def legacy(postgres_container):
    """Base con forma de legacy, sembrada con problemas equivalentes a los reales.

    Cada prueba usa una base propia: los scripts modifican datos, y compartirla
    haría que el resultado dependiera del orden.
    """
    if not shutil.which("docker"):
        pytest.skip("hace falta docker")

    contenedor = postgres_container.get_wrapped_container().id
    base = f"legacy_{uuid.uuid4().hex[:10]}"
    usuario = postgres_container.username
    entorno = {
        "PGUSER": usuario,
        "PGPASSWORD": postgres_container.password,
        "PGHOST": "localhost",
    }

    def ejecutar(orden: list[str], **extra) -> subprocess.CompletedProcess:
        variables: list[str] = []
        for clave, valor in {**entorno, **extra}.items():
            variables += ["--env", f"{clave}={valor}"]
        return subprocess.run(
            ["docker", "exec", *variables, contenedor, *orden],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )

    def copiar(origen: Path, destino: str) -> None:
        subprocess.run(
            ["docker", "cp", str(origen), f"{contenedor}:{destino}"],
            check=True,
            timeout=60,
            capture_output=True,
        )

    def psql(sql: str, *, base_datos: str | None = None) -> str:
        resultado = ejecutar(
            ["psql", "--dbname", base_datos or base, "-tAc", sql, "-v", "ON_ERROR_STOP=1"]
        )
        assert resultado.returncode == 0, resultado.stderr
        return resultado.stdout.strip()

    def correr_script(ruta_en_contenedor: str, *variables: str) -> subprocess.CompletedProcess:
        argumentos = []
        for v in variables:
            argumentos += ["-v", v]
        return ejecutar(
            [
                "psql",
                "--dbname",
                base,
                "-v",
                "ON_ERROR_STOP=1",
                *argumentos,
                "-f",
                ruta_en_contenedor,
            ]
        )

    ejecutar(["createdb", base])
    for archivo in ("legacy_schema.sql", "legacy_seed.sql"):
        copiar(_FIXTURES / archivo, f"/tmp/{archivo}")
        resultado = correr_script(f"/tmp/{archivo}")
        assert resultado.returncode == 0, f"{archivo}: {resultado.stderr}"

    for script in _CORRECCIONES.glob("*.sql"):
        copiar(script, f"/tmp/{script.name}")

    yield {
        "base": base,
        "psql": psql,
        "correr": correr_script,
        "copiar": copiar,
        "ejecutar": ejecutar,
    }

    ejecutar(["dropdb", "--if-exists", base])


def _con_decisiones(decisiones: str) -> Path:
    """Copia de 001_emails.sql con las decisiones cargadas.

    Es como lo va a usar una persona: el script trae el bloque vacío y las
    decisiones se escriben dentro, para que queden versionadas en git.
    """
    original = (_CORRECCIONES / "001_emails.sql").read_text()
    marcador = "-- ###########################################################################\n\n-- Nada que hacer"
    assert marcador in original, "cambió la estructura de 001_emails.sql"

    contenido = original.replace(
        marcador,
        f"-- ###########################################################################\n{decisiones}\n\n-- Nada que hacer",
    ).replace(
        "\\set decidido_por 'PENDIENTE — poner el nombre de quien decide'",
        "\\set decidido_por 'prueba automatizada'",
    )

    destino = Path("/tmp") / f"001_emails_{uuid.uuid4().hex[:8]}.sql"
    destino.write_text(contenido)
    return destino


class TestCoberturaDeArchivos:
    """Ninguna columna de archivo del legacy puede quedar sin migrar.

    Existe por un caso real: `core_warehouse.uploaded_file` guardaba el
    Warehouse Receipt colgado de la propia fila, sin pasar por
    `core_warehousedocument`. El migrador leía las tres tablas de documentos y
    esa columna no, así que 221 archivos —casi 10 GB, el grueso del archivo
    histórico— se perdían en el corte sin que nada lo dijera.

    No se detectó antes por dos motivos que esta clase corrige: el esquema de
    prueba no tenía la columna, y ninguna prueba miraba la cobertura.
    """

    def test_el_esquema_de_prueba_tiene_las_columnas_de_archivo_reales(self, legacy) -> None:
        """El fixture tiene que parecerse a producción o no prueba nada.

        Si el esquema de prueba pierde una columna que producción sí tiene, las
        pruebas siguen en verde mientras el migrador ignora datos reales. Es
        exactamente lo que pasó.
        """
        columnas = set(
            legacy["psql"]("""
                SELECT table_name || '.' || column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND (column_name LIKE '%file%' OR column_name = 'uploaded_file')
                ORDER BY 1
            """).splitlines()
        )

        assert "core_warehouse.uploaded_file" in columnas
        assert "core_warehousedocument.file" in columnas

    def test_el_migrador_lee_toda_columna_de_archivo(self, legacy) -> None:
        """Cada columna que guarda una ruta tiene que aparecer en el migrador.

        Es una comprobación de texto y no de comportamiento, a propósito: lo que
        falló no fue una consulta mal escrita sino una tabla que nadie miró, y
        eso no se ve ejecutando lo que sí se escribió.
        """
        fuente = (_RAIZ / "backend/scripts/migrate_legacy.py").read_text()

        columnas = [
            fila.split(".", 1)
            for fila in legacy["psql"]("""
                SELECT table_name || '.' || column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND (column_name LIKE '%file%' OR column_name = 'uploaded_file')
                ORDER BY 1
            """).splitlines()
            if fila
        ]

        assert columnas, "la semilla dejó de tener columnas de archivo"

        sin_cubrir = [
            f"{tabla}.{columna}"
            for tabla, columna in columnas
            if tabla not in fuente or columna not in fuente
        ]

        assert not sin_cubrir, (
            "estas columnas guardan rutas de archivo y el migrador no las lee; "
            f"sus archivos se perderían en el cutover: {sin_cubrir}"
        )

    def test_los_adjuntos_sembrados_cubren_zip_pdf_y_docx(self, legacy) -> None:
        """El ZIP es el formato que ningún tipo de documento acepta.

        Los 190 Warehouse Receipt comprimidos del legacy entran igual porque el
        migrador escribe en `documents` directo, sin pasar por la validación de
        formato. Si eso cambiara, esta semilla lo destapa.
        """
        extensiones = set(
            legacy["psql"]("""
                SELECT DISTINCT lower(split_part(uploaded_file, '.', -1))
                FROM core_warehouse
                WHERE uploaded_file IS NOT NULL AND uploaded_file <> ''
            """).splitlines()
        )

        assert extensiones == {"zip", "pdf", "docx"}


class TestDiagnostico:
    def test_encuentra_todos_los_problemas_del_inventario(self, legacy) -> None:
        legacy["copiar"](_RAIZ / "docs/migration/diagnostico_5_1.sql", "/tmp/diagnostico.sql")

        resultado = legacy["correr"]("/tmp/diagnostico.sql")

        assert resultado.returncode == 0, resultado.stderr
        salida = resultado.stdout
        # La comprobación previa de esquema no debe reportar columnas faltantes.
        assert "FALTA — revisar el esquema real" not in salida
        # Y el resumen final tiene que contar lo mismo que el inventario.
        assert "usuarios con email vacío" in salida
        assert "cargas sin cliente" in salida

    def test_no_modifica_nada(self, legacy) -> None:
        """Se corre contra producción para decidir: leer tiene que ser seguro."""
        antes = legacy["psql"](
            "SELECT count(*) || ':' || count(*) FILTER (WHERE email = '') FROM auth_user"
        )
        legacy["copiar"](_RAIZ / "docs/migration/diagnostico_5_1.sql", "/tmp/diagnostico.sql")

        legacy["correr"]("/tmp/diagnostico.sql")

        assert (
            legacy["psql"](
                "SELECT count(*) || ':' || count(*) FILTER (WHERE email = '') FROM auth_user"
            )
            == antes
        )


class TestCorreccionDeCorreos:
    def _decisiones_completas(self) -> str:
        return """
INSERT INTO decisiones VALUES (1, 'sin.correo@empresa-alfa.example', false, 'Cuenta en uso con cargas');
INSERT INTO decisiones VALUES (2, NULL, true, 'Sin accesos y ya inactiva');
INSERT INTO decisiones VALUES (3, NULL, true, 'Sin accesos ni cargas');
INSERT INTO decisiones VALUES (4, 'staff@empresa-alfa.example', false, 'Cuenta de staff en uso');
INSERT INTO decisiones VALUES (6, NULL, true, 'Duplicado de ana; sin accesos');
INSERT INTO decisiones VALUES (8, NULL, true, 'Duplicado de operaciones; ya inactiva');
"""

    def test_sin_decisiones_aborta(self, legacy) -> None:
        """Correrlo vacío no debe dar la impresión de que corrigió algo."""
        resultado = legacy["correr"]("/tmp/001_emails.sql")

        assert resultado.returncode != 0
        assert "No hay decisiones cargadas" in resultado.stderr

    def test_deja_los_correos_sin_vacios_ni_duplicados(self, legacy) -> None:
        script = _con_decisiones(self._decisiones_completas())
        legacy["copiar"](script, "/tmp/001_con_decisiones.sql")
        legacy["correr"]("/tmp/000_registro.sql")

        resultado = legacy["correr"]("/tmp/001_con_decisiones.sql")

        assert resultado.returncode == 0, resultado.stderr
        assert (
            legacy["psql"](
                "SELECT count(*) FROM auth_user WHERE is_active AND coalesce(trim(email),'') = ''"
            )
            == "0"
        )
        assert (
            legacy["psql"]("""
                SELECT count(*) FROM (
                    SELECT lower(trim(email)) FROM auth_user
                    WHERE is_active AND coalesce(trim(email),'') <> ''
                    GROUP BY 1 HAVING count(*) > 1
                ) d
            """)
            == "0"
        )

    def test_registra_cada_cambio_con_su_valor_anterior(self, legacy) -> None:
        """Sin el valor anterior no se puede revertir ni auditar qué pasó."""
        script = _con_decisiones(self._decisiones_completas())
        legacy["copiar"](script, "/tmp/001_con_decisiones.sql")
        legacy["correr"]("/tmp/000_registro.sql")
        legacy["correr"]("/tmp/001_con_decisiones.sql")

        registros = legacy["psql"](
            "SELECT count(*) FROM migracion_correcciones WHERE script = '001_emails'"
        )
        sin_motivo = legacy["psql"](
            "SELECT count(*) FROM migracion_correcciones WHERE coalesce(trim(motivo),'') = ''"
        )

        # Dos correos nuevos (usuarios 1 y 4) y dos desactivaciones (3 y 6). Las
        # cuentas 2 y 8 ya estaban inactivas, así que no generan registro: no
        # hubo cambio que anotar ni nada que revertir.
        assert int(registros) == 4
        assert sin_motivo == "0"

    def test_un_correo_que_chocaria_con_otro_usuario_aborta(self, legacy) -> None:
        """Es el error que el script existe para impedir."""
        legacy["correr"]("/tmp/000_registro.sql")
        script = _con_decisiones(
            "INSERT INTO decisiones VALUES (1, 'usuario9@ejemplo.example', false, 'choca');"
        )
        legacy["copiar"](script, "/tmp/001_choque.sql")

        resultado = legacy["correr"]("/tmp/001_choque.sql")

        assert resultado.returncode != 0
        assert "chocarían con otro usuario" in resultado.stderr
        # Y no dejó nada a medias.
        assert legacy["psql"]("SELECT email FROM auth_user WHERE id = 1") == ""

    def test_dos_decisiones_con_el_mismo_correo_abortan(self, legacy) -> None:
        legacy["correr"]("/tmp/000_registro.sql")
        script = _con_decisiones(
            "INSERT INTO decisiones VALUES (1, 'nuevo@ejemplo.example', false, 'a');\n"
            "INSERT INTO decisiones VALUES (3, 'nuevo@ejemplo.example', false, 'b');"
        )
        legacy["copiar"](script, "/tmp/001_doble.sql")

        resultado = legacy["correr"]("/tmp/001_doble.sql")

        assert resultado.returncode != 0

    def test_correrlo_dos_veces_no_duplica_el_registro(self, legacy) -> None:
        script = _con_decisiones(self._decisiones_completas())
        legacy["copiar"](script, "/tmp/001_con_decisiones.sql")
        legacy["correr"]("/tmp/000_registro.sql")
        legacy["correr"]("/tmp/001_con_decisiones.sql")
        primera = legacy["psql"]("SELECT count(*) FROM migracion_correcciones")

        legacy["correr"]("/tmp/001_con_decisiones.sql")

        assert legacy["psql"]("SELECT count(*) FROM migracion_correcciones") == primera


class TestCargasSinCliente:
    def test_sin_asignaciones_las_deja_para_revision(self, legacy) -> None:
        """Camino B: no inventar una atribución es una decisión válida."""
        legacy["correr"]("/tmp/000_registro.sql")

        resultado = legacy["correr"]("/tmp/002_cargas_sin_cliente.sql")

        assert resultado.returncode == 0, resultado.stderr
        assert legacy["psql"]("SELECT count(*) FROM core_warehouse WHERE cliente_id IS NULL") == "5"

    def test_rechaza_asignar_un_usuario_de_otra_empresa(self, legacy) -> None:
        """Es la fuga que el modelo de alcances existe para impedir."""
        legacy["correr"]("/tmp/000_registro.sql")
        original = (_CORRECCIONES / "002_cargas_sin_cliente.sql").read_text()
        # WR000001 es de la empresa 2; el usuario 5 pertenece a la empresa 1.
        contenido = original.replace(
            "-- ###########################################################################\n\n-- El cliente asignado",
            "-- ###########################################################################\n"
            "INSERT INTO asignaciones VALUES ('WR000001', 5, 'asignación equivocada');\n\n"
            "-- El cliente asignado",
        )
        ruta = Path("/tmp") / f"002_malo_{uuid.uuid4().hex[:8]}.sql"
        ruta.write_text(contenido)
        legacy["copiar"](ruta, "/tmp/002_malo.sql")

        resultado = legacy["correr"]("/tmp/002_malo.sql")

        assert resultado.returncode != 0
        assert "otra empresa" in resultado.stderr
        assert (
            legacy["psql"](
                "SELECT coalesce(cliente_id::text, '') FROM core_warehouse "
                "WHERE wr_number = 'WR000001'"
            )
            == ""
        )


class TestNormalizacionDelWrNoSeHaceAqui:
    """El WR mal capturado se deja como está en el legacy.

    Es la clave primaria y lo referencian cinco tablas con `NO ACTION`, así que
    cambiarlo en una carga con documentos o piezas falla. Se normaliza al
    migrar, donde ya es una referencia y no la identidad (ADR-0005).
    """

    def test_ya_no_existe_un_script_que_lo_toque(self) -> None:
        assert not (_CORRECCIONES / "003_wr_formato.sql").exists()
        # Y queda escrito por qué, para que nadie lo reponga sin leer el motivo.
        assert (_CORRECCIONES / "003_wr_formato.OMITIDO.md").exists()

    def test_cambiar_el_wr_en_el_legacy_falla_de_verdad(self, legacy) -> None:
        """No es una precaución teórica: la base lo rechaza."""
        resultado = legacy["ejecutar"](
            [
                "psql",
                "--dbname",
                legacy["base"],
                "-v",
                "ON_ERROR_STOP=1",
                "-c",
                "UPDATE core_warehouse SET wr_number = 'WR000903' WHERE wr_number = 'WR000901.'",
            ]
        )

        assert resultado.returncode != 0
        assert "foreign key" in resultado.stderr.lower()

    def test_el_migrador_si_lo_normaliza(self) -> None:
        from scripts.migrate_legacy import normalizar_wr

        assert normalizar_wr("WR000901.") == "WR000901"
        assert normalizar_wr("WR000902|") == "WR000902"


class TestReversion:
    def test_devuelve_los_datos_a_como_estaban(self, legacy) -> None:
        """Un script de corrección sin vuelta atrás obliga a restaurar un
        respaldo entero para deshacer un correo mal puesto."""
        legacy["correr"]("/tmp/000_registro.sql")
        # Las decisiones tienen que cubrir a TODOS los que quedarían sin correo:
        # el script verifica antes de confirmar, y si alguno queda vacío aborta
        # y no hay nada que revertir.
        script = _con_decisiones(
            "INSERT INTO decisiones VALUES (1, 'nuevo@ejemplo.example', false, 'prueba');\n"
            "INSERT INTO decisiones VALUES (2, NULL, true, 'sin uso');\n"
            "INSERT INTO decisiones VALUES (3, NULL, true, 'sin uso');\n"
            "INSERT INTO decisiones VALUES (4, NULL, true, 'sin uso');\n"
            "INSERT INTO decisiones VALUES (6, NULL, true, 'duplicado');\n"
            "INSERT INTO decisiones VALUES (8, NULL, true, 'duplicado');"
        )
        legacy["copiar"](script, "/tmp/001_revertible.sql")

        antes = legacy["psql"]("SELECT coalesce(email, '') FROM auth_user WHERE id = 1")
        legacy["correr"]("/tmp/001_revertible.sql")
        assert legacy["psql"]("SELECT email FROM auth_user WHERE id = 1") == "nuevo@ejemplo.example"

        resultado = legacy["correr"]("/tmp/999_revertir.sql", "script=001_emails")

        assert resultado.returncode == 0, resultado.stderr
        assert legacy["psql"]("SELECT coalesce(email, '') FROM auth_user WHERE id = 1") == antes
        assert (
            legacy["psql"](
                "SELECT count(*) FROM migracion_correcciones WHERE script = '001_emails'"
            )
            == "0"
        )

    def test_revertir_algo_no_aplicado_avisa(self, legacy) -> None:
        legacy["correr"]("/tmp/000_registro.sql")

        resultado = legacy["correr"]("/tmp/999_revertir.sql", "script=001_emails")

        assert resultado.returncode != 0
        assert "No hay correcciones registradas" in resultado.stderr

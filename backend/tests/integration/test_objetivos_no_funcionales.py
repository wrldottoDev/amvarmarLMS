"""Objetivos no funcionales del proyecto (Paso 4.3).

Dos cosas que solo se pueden comprobar ejecutando:

- **p95 < 500 ms** en lecturas comunes, sin contar uploads ni proveedores
  externos.
- **Restauración de un respaldo** en una base limpia, que es lo que convierte
  un archivo en un respaldo de verdad.

Ambas están marcadas `slow`: una hace cientos de peticiones y la otra levanta
un PostgreSQL aparte.
"""

import os
import re
import shutil
import statistics
import subprocess
import time
import uuid
from pathlib import Path

import httpx
import pytest
from scripts.seed_rbac import sembrar as sembrar_rbac
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security.argon2 import hash_password

pytestmark = pytest.mark.integration

PASSWORD = "Contrasena-De-Pruebas-1"

# Objetivo del proyecto. Si esto se relaja, se relaja el compromiso.
P95_OBJETIVO_MS = 500.0

# Versión del contenedor que levanta la suite (`postgres:16-alpine`).
_VERSION_SERVIDOR_PG = 16


@pytest.fixture
async def usuario_con_cargas(db_directa: AsyncSession):
    """Un cliente con suficientes cargas para que el listado no sea trivial."""
    await sembrar_rbac(db_directa)
    from scripts.seed_shipment_statuses import sembrar as sembrar_estados

    await sembrar_estados(db_directa)

    marca = uuid.uuid4().hex[:8]
    email = f"carga-{marca}@pruebas.amvarmar.com"

    empresa = (
        await db_directa.execute(
            text("INSERT INTO companies (legal_name, status) VALUES (:n,'ACTIVE') RETURNING id"),
            {"n": f"Carga {marca} S.A."},
        )
    ).scalar_one()
    user_id = (
        await db_directa.execute(
            text("""
                INSERT INTO users (email, password_hash, first_name, last_name, status)
                VALUES (:e, :h, 'N', 'A', 'ACTIVE') RETURNING id
            """),
            {"e": email, "h": hash_password(PASSWORD)},
        )
    ).scalar_one()
    await db_directa.execute(
        text("""
            INSERT INTO user_role_assignments (user_id, role_id, scope_type, company_id)
            SELECT :u, r.id, 'ORGANIZATION', :c FROM roles r WHERE r.code = 'CLIENT_ADMIN'
        """),
        {"u": user_id, "c": empresa},
    )
    await db_directa.execute(
        text(
            "INSERT INTO company_memberships (company_id, user_id, status) VALUES (:c,:u,'ACTIVE')"
        ),
        {"c": empresa, "u": user_id},
    )

    origen = (
        await db_directa.execute(
            text("""
                INSERT INTO locations (country_code, city_code, location_code, name)
                VALUES ('US','MIA','US-MIA','Miami')
                ON CONFLICT (location_code) DO UPDATE SET name = EXCLUDED.name
                RETURNING id
            """)
        )
    ).scalar_one()

    await db_directa.execute(
        text("""
            INSERT INTO shipments
                (company_id, created_by, current_status_code, origin_location_id,
                 destination_location_id)
            SELECT :c, :u, 'STORED', :o, :o FROM generate_series(1, 200)
        """),
        {"c": empresa, "u": user_id, "o": origen},
    )
    # Toda carga activa necesita al menos una pieza. Se siembran en bloque
    # porque estas inserciones son masivas y no devuelven ids.
    await db_directa.execute(
        text("""
            INSERT INTO shipment_packages (shipment_id, package_type, quantity)
            SELECT s.id, 'BOX', 1 FROM shipments s WHERE s.company_id = :c
              AND NOT EXISTS (SELECT 1 FROM shipment_packages p WHERE p.shipment_id = s.id)
        """),
        {"c": empresa},
    )
    await db_directa.commit()

    yield {"email": email, "empresa": empresa, "user_id": user_id}

    await db_directa.execute(
        text(
            "DELETE FROM shipment_events WHERE shipment_id IN "
            "(SELECT id FROM shipments WHERE company_id = :c)"
        ),
        {"c": empresa},
    )
    await db_directa.execute(text("DELETE FROM shipments WHERE company_id = :c"), {"c": empresa})
    await db_directa.execute(
        text("DELETE FROM company_memberships WHERE company_id = :c"), {"c": empresa}
    )
    await db_directa.execute(
        text("DELETE FROM user_role_assignments WHERE user_id = :u"), {"u": user_id}
    )
    await db_directa.execute(text("DELETE FROM users WHERE id = :u"), {"u": user_id})
    await db_directa.execute(text("DELETE FROM companies WHERE id = :c"), {"c": empresa})
    await db_directa.commit()


@pytest.mark.slow
class TestLatencia:
    async def test_p95_de_lecturas_bajo_el_objetivo(
        self, cliente: httpx.AsyncClient, usuario_con_cargas: dict
    ) -> None:
        """p95 < 500 ms en las lecturas que más se usan.

        Se mide contra la app real con su base real, no con dobles: el tiempo
        que importa es el de la consulta, y un doble lo eliminaría justo.

        La medición es un piso, no un techo: en producción hay red, más datos y
        concurrencia. Si acá ya no se cumple, en producción tampoco.
        """
        respuesta = await cliente.post(
            "/api/v1/auth/login",
            json={"email": usuario_con_cargas["email"], "password": PASSWORD},
        )
        cabeceras = {"Authorization": f"Bearer {respuesta.json()['access_token']}"}

        rutas = [
            "/api/v1/shipments?limit=25",
            "/api/v1/dashboard/client",
            "/api/v1/notifications?limit=25",
            "/api/v1/me",
        ]

        # Calentamiento: la primera petición paga la construcción del pool y la
        # compilación de las consultas, que no representa el estado estable.
        for ruta in rutas:
            await cliente.get(ruta, headers=cabeceras)

        muestras: dict[str, list[float]] = {r: [] for r in rutas}
        for _ in range(30):
            for ruta in rutas:
                inicio = time.perf_counter()
                r = await cliente.get(ruta, headers=cabeceras)
                muestras[ruta].append((time.perf_counter() - inicio) * 1000)
                assert r.status_code == 200, f"{ruta} devolvió {r.status_code}"

        fallos = []
        for ruta, tiempos in muestras.items():
            p95 = statistics.quantiles(tiempos, n=20)[18]
            print(
                f"\n{ruta}: p50={statistics.median(tiempos):.1f} ms  "
                f"p95={p95:.1f} ms  max={max(tiempos):.1f} ms"
            )
            if p95 >= P95_OBJETIVO_MS:
                fallos.append(f"{ruta}: p95 {p95:.1f} ms")

        assert not fallos, f"Rutas sobre el objetivo de {P95_OBJETIVO_MS} ms: {fallos}"

    async def test_la_paginacion_no_se_degrada_con_la_profundidad(
        self, cliente: httpx.AsyncClient, usuario_con_cargas: dict
    ) -> None:
        """La página 8 debe costar lo mismo que la 1.

        Es la razón de paginar por cursor y no por `OFFSET`: con offset, cada
        página recorre todo lo anterior y la última es la más cara.
        """
        respuesta = await cliente.post(
            "/api/v1/auth/login",
            json={"email": usuario_con_cargas["email"], "password": PASSWORD},
        )
        cabeceras = {"Authorization": f"Bearer {respuesta.json()['access_token']}"}

        tiempos: list[float] = []
        cursor = None
        for _ in range(8):
            url = "/api/v1/shipments?limit=25" + (f"&cursor={cursor}" if cursor else "")
            inicio = time.perf_counter()
            cuerpo = (await cliente.get(url, headers=cabeceras)).json()
            tiempos.append((time.perf_counter() - inicio) * 1000)
            cursor = cuerpo["next_cursor"]
            if cursor is None:
                break

        print(f"\npáginas: {[f'{t:.0f}' for t in tiempos]} ms")
        # La última no debe costar más del triple que la primera. Con OFFSET la
        # diferencia crece sin techo; el margen absorbe el ruido de medición.
        assert tiempos[-1] < tiempos[0] * 3 + 50


def _hay_docker() -> bool:
    return shutil.which("docker") is not None


@pytest.mark.slow
@pytest.mark.skipif(not _hay_docker(), reason="hace falta docker")
class TestRespaldoYRestauracion:
    """Un respaldo que nunca se restauró no es un respaldo: es un archivo.

    Los scripts corren DENTRO del contenedor de PostgreSQL, no en el host. Dos
    razones: `pg_dump` se niega a respaldar un servidor más nuevo que él (el
    host puede tener herramientas viejas, y de hecho las tiene), y así se prueba
    contra exactamente la versión que corre en producción.
    """

    def _ejecutar(self, contenedor: str, orden: list[str], entorno: dict[str, str]):
        variables: list[str] = []
        for clave, valor in entorno.items():
            variables += ["--env", f"{clave}={valor}"]
        return subprocess.run(
            ["docker", "exec", *variables, contenedor, *orden],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )

    def _copiar_scripts(self, contenedor: str) -> None:
        raiz = Path(__file__).resolve().parents[3]
        for script in ("respaldar.sh", "ensayar_restauracion.sh"):
            subprocess.run(
                [
                    "docker",
                    "cp",
                    str(raiz / "infra/backup" / script),
                    f"{contenedor}:/tmp/{script}",
                ],
                check=True,
                timeout=60,
                capture_output=True,
            )

    def _entorno(self, contenedor_pg) -> dict[str, str]:
        return {
            "PGHOST": "localhost",
            "PGUSER": contenedor_pg.username,
            "PGPASSWORD": contenedor_pg.password,
            "PGDATABASE": contenedor_pg.dbname,
            "BACKUP_DIR": "/tmp/respaldos",
        }

    async def test_respaldar_y_restaurar_conserva_los_datos(
        self, db_directa: AsyncSession, postgres_container, migrated_database: str
    ) -> None:
        """El ensayo completo: respaldar, restaurar en base limpia, verificar.

        Comprueba lo mismo que el script de ensayo comprueba en producción: que
        las tablas no quedan vacías. Restaurar solo el esquema también
        "funciona", y sería un desastre descubrirlo durante una emergencia.
        """
        await sembrar_rbac(db_directa)
        from scripts.seed_shipment_statuses import sembrar as sembrar_estados

        await sembrar_estados(db_directa)

        marca = uuid.uuid4().hex[:8]
        nombre = f"Respaldo {marca} S.A."
        # El ensayo comprueba que `users`, `companies`, `shipments` y
        # `audit_logs` no queden vacías. Hay que sembrar las cuatro: si no, el
        # ensayo falla con razón y no se prueba nada del respaldo.
        empresa = (
            await db_directa.execute(
                text(
                    "INSERT INTO companies (legal_name, status) VALUES (:n,'ACTIVE') RETURNING id"
                ),
                {"n": nombre},
            )
        ).scalar_one()
        usuario = (
            await db_directa.execute(
                text("""
                    INSERT INTO users (email, password_hash, first_name, last_name, status)
                    VALUES (:e,'h','N','A','ACTIVE') RETURNING id
                """),
                {"e": f"respaldo-{marca}@pruebas.amvarmar.com"},
            )
        ).scalar_one()
        ubicacion = (
            await db_directa.execute(
                text("""
                    INSERT INTO locations (country_code, city_code, location_code, name)
                    VALUES ('US','MIA','US-MIA','Miami')
                    ON CONFLICT (location_code) DO UPDATE SET name = EXCLUDED.name
                    RETURNING id
                """)
            )
        ).scalar_one()
        await db_directa.execute(
            text("""
                INSERT INTO shipments
                    (company_id, created_by, current_status_code, origin_location_id,
                     destination_location_id)
                VALUES (:c,:u,'STORED',:o,:o)
            """),
            {"c": empresa, "u": usuario, "o": ubicacion},
        )
        # Toda carga activa necesita al menos una pieza.
        await db_directa.execute(
            text("""
                INSERT INTO shipment_packages (shipment_id, package_type, quantity)
                SELECT s.id, 'BOX', 1 FROM shipments s WHERE s.company_id = :c
                  AND NOT EXISTS (SELECT 1 FROM shipment_packages p WHERE p.shipment_id = s.id)
            """),
            {"c": empresa},
        )
        # `audit_logs` no se llena sola: este test usa `db_directa`, no
        # `cliente`, así que no hay request HTTP que audite nada. Antes
        # dependía de filas que dejaban otros tests del run; ahora que cada
        # test limpia lo suyo (ver `conftest._vaciar_datos_de_prueba`), hay
        # que sembrarla explícitamente como al resto de las tablas que el
        # ensayo verifica.
        await db_directa.execute(
            text("""
                INSERT INTO audit_logs
                    (actor_user_id, company_id, action, resource_type, outcome)
                VALUES (:u, :c, 'test.respaldo', 'company', 'SUCCESS')
            """),
            {"u": usuario, "c": empresa},
        )
        await db_directa.commit()

        contenedor = postgres_container.get_wrapped_container().id
        entorno = self._entorno(postgres_container)
        self._copiar_scripts(contenedor)

        try:
            respaldo = self._ejecutar(contenedor, ["bash", "/tmp/respaldar.sh"], entorno)
            assert respaldo.returncode == 0, respaldo.stderr

            listado = self._ejecutar(
                contenedor, ["ls", "-1", entorno["BACKUP_DIR"]], entorno
            ).stdout.split()
            dumps = [a for a in listado if a.endswith(".dump")]
            assert len(dumps) == 1, f"se esperaba un respaldo, hay {dumps}"
            # La suma de control se calcula al respaldar: verificarla antes de
            # restaurar detecta corrupción en reposo o en el traslado.
            assert f"{dumps[0]}.sha256" in listado
            # Un `.parcial` con nombre definitivo haría que la próxima
            # restauración usara un archivo truncado creyéndolo bueno.
            assert not [a for a in listado if a.endswith(".parcial")]

            # El ensayo hace el ciclo completo: verifica integridad, restaura en
            # una base desechable y cuenta filas.
            ensayo = self._ejecutar(contenedor, ["bash", "/tmp/ensayar_restauracion.sh"], entorno)
            assert ensayo.returncode == 0, ensayo.stdout + ensayo.stderr
            assert "ENSAYO CORRECTO" in ensayo.stdout
            # Y deja el dato del RTO real, que es lo que hace útil al ensayo.
            assert "restauración completa en" in ensayo.stdout
        finally:
            await db_directa.execute(
                text("DELETE FROM shipments WHERE company_id = :c"), {"c": empresa}
            )
            await db_directa.execute(text("DELETE FROM users WHERE id = :u"), {"u": usuario})
            await db_directa.execute(text("DELETE FROM companies WHERE id = :c"), {"c": empresa})
            await db_directa.commit()
            self._ejecutar(contenedor, ["rm", "-rf", entorno["BACKUP_DIR"]], entorno)

    async def test_el_ensayo_rechaza_un_respaldo_corrupto(self, postgres_container) -> None:
        """La suma de control existe para esto.

        Sin verificarla, el ensayo pasaría contra un archivo truncado y daría
        una tranquilidad falsa, que es peor que no tener ensayo.
        """
        contenedor = postgres_container.get_wrapped_container().id
        entorno = self._entorno(postgres_container)
        entorno["BACKUP_DIR"] = "/tmp/respaldos-corruptos"
        self._copiar_scripts(contenedor)

        try:
            assert (
                self._ejecutar(contenedor, ["bash", "/tmp/respaldar.sh"], entorno).returncode == 0
            )

            # Corromper el archivo SIN tocar su suma de control.
            self._ejecutar(
                contenedor,
                [
                    "bash",
                    "-c",
                    f"archivo=$(ls {entorno['BACKUP_DIR']}/*.dump); "
                    'truncate -s -2000 "$archivo"; printf basura >> "$archivo"',
                ],
                entorno,
            )

            ensayo = self._ejecutar(contenedor, ["bash", "/tmp/ensayar_restauracion.sh"], entorno)

            assert ensayo.returncode != 0, "el ensayo aceptó un respaldo corrupto"
            assert "sha256" in (ensayo.stdout + ensayo.stderr).lower()
        finally:
            self._ejecutar(contenedor, ["rm", "-rf", entorno["BACKUP_DIR"]], entorno)

    async def test_el_script_avisa_si_las_herramientas_son_viejas(self) -> None:
        """El host tiene pg_dump 14 y el servidor es 16: caso real, no hipotético.

        `pg_dump` falla con un error que no dice qué hacer. El script comprueba
        antes y da un mensaje accionable, porque una máquina de respaldos con
        herramientas viejas produce cero respaldos y nadie lo nota hasta que
        hace falta restaurar.
        """
        if not _hay_herramientas_pg():
            pytest.skip("no hay herramientas pg en el host")
        if _version_cliente_pg() >= _VERSION_SERVIDOR_PG:
            pytest.skip("el host ya tiene herramientas al día")

        from urllib.parse import urlparse

        entorno = dict(os.environ)
        entorno["BACKUP_DIR"] = "/tmp/no-deberia-crearse"

        # Se apunta al contenedor de la suite, que es más nuevo que el cliente.
        url = os.environ.get("DATABASE_URL", "")
        if not url:
            # Sin URL, los `or` de abajo apuntarían a un `postgres@localhost`
            # que no tiene por qué existir, y el script fallaría por no poder
            # conectarse. Eso no dice nada sobre la comprobación de versiones,
            # que es lo único que esta prueba mira.
            pytest.skip("DATABASE_URL no está en el entorno")

        partes = urlparse(url.replace("postgresql+asyncpg://", "postgresql://"))
        entorno.update(
            {
                "PGHOST": partes.hostname or "localhost",
                "PGPORT": str(partes.port or 5432),
                "PGUSER": partes.username or "postgres",
                "PGPASSWORD": partes.password or "",
                "PGDATABASE": (partes.path or "/postgres").lstrip("/"),
            }
        )

        raiz = Path(__file__).resolve().parents[3]
        resultado = subprocess.run(
            ["bash", str(raiz / "infra/backup/respaldar.sh")],
            env=entorno,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )

        if resultado.returncode == 0:
            pytest.skip("la base local no es más nueva que el cliente")

        assert "pg_dump no puede respaldar un servidor más nuevo" in resultado.stderr


def _hay_herramientas_pg() -> bool:
    return all(shutil.which(h) for h in ("pg_dump", "pg_restore", "psql", "createdb"))


def _version_cliente_pg() -> int:
    salida = subprocess.run(
        ["pg_dump", "--version"], capture_output=True, text=True, check=True, timeout=30
    ).stdout
    coincidencia = re.search(r"(\d+)", salida)
    return int(coincidencia.group(1)) if coincidencia else 0

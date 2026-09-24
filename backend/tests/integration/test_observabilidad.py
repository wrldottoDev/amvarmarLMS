"""Métricas y protección del endpoint (Paso 4.3)."""

import os
import uuid

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import get_settings
from app.core.observability import metrics

pytestmark = pytest.mark.integration


@pytest.fixture
def sin_token():
    """Entorno con `/metrics` deshabilitado, como un despliegue sin configurar."""
    anteriores = {
        "METRICS_TOKEN": os.environ.get("METRICS_TOKEN"),
        "ENVIRONMENT": os.environ.get("ENVIRONMENT"),
    }
    os.environ.pop("METRICS_TOKEN", None)
    os.environ["ENVIRONMENT"] = "produccion"
    get_settings.cache_clear()

    yield

    for clave, valor in anteriores.items():
        if valor is None:
            os.environ.pop(clave, None)
        else:
            os.environ[clave] = valor
    get_settings.cache_clear()


@pytest.fixture
def con_token():
    anteriores = {
        "METRICS_TOKEN": os.environ.get("METRICS_TOKEN"),
        "ENVIRONMENT": os.environ.get("ENVIRONMENT"),
    }
    token = f"token-de-pruebas-{uuid.uuid4().hex}"
    os.environ["METRICS_TOKEN"] = token
    os.environ["ENVIRONMENT"] = "produccion"
    get_settings.cache_clear()

    yield token

    for clave, valor in anteriores.items():
        if valor is None:
            os.environ.pop(clave, None)
        else:
            os.environ[clave] = valor
    get_settings.cache_clear()


class TestProteccionDelEndpoint:
    """`/metrics` expone rutas internas y patrones de fallo de login."""

    async def test_sin_token_configurado_queda_deshabilitado(
        self, cliente: httpx.AsyncClient, sin_token
    ) -> None:
        """Fail closed: un despliegue que olvida la variable no lo publica.

        404 y no 403: que el endpoint exista ya es información.
        """
        r = await cliente.get("/metrics")

        assert r.status_code == 404

    async def test_con_token_configurado_exige_el_token(
        self, cliente: httpx.AsyncClient, con_token: str
    ) -> None:
        assert (await cliente.get("/metrics")).status_code == 401

    async def test_un_token_incorrecto_no_pasa(
        self, cliente: httpx.AsyncClient, con_token: str
    ) -> None:
        r = await cliente.get("/metrics", headers={"Authorization": "Bearer token-equivocado"})

        assert r.status_code == 401

    async def test_el_token_correcto_devuelve_las_metricas(
        self, cliente: httpx.AsyncClient, con_token: str
    ) -> None:
        r = await cliente.get("/metrics", headers={"Authorization": f"Bearer {con_token}"})

        assert r.status_code == 200
        assert "text/plain" in r.headers["content-type"]
        assert "amvarmar_outbox_pendiente" in r.text

    async def test_acepta_el_token_sin_el_prefijo_bearer(
        self, cliente: httpx.AsyncClient, con_token: str
    ) -> None:
        """Prometheus con `credentials_file` manda el valor pelado."""
        r = await cliente.get("/metrics", headers={"Authorization": con_token})

        assert r.status_code == 200


@pytest.fixture
async def motor_de_prueba(migrated_database: str):
    """Motor async contra el contenedor, para el refresco de indicadores.

    El bind de la sesión de prueba es el motor síncrono; el refresco necesita
    uno asíncrono.
    """
    motor = create_async_engine(migrated_database)
    yield motor
    await motor.dispose()


class TestIndicadoresDerivados:
    async def test_reflejan_el_estado_de_la_base(
        self, db_directa: AsyncSession, motor_de_prueba
    ) -> None:
        from app.modules.audit.outbox import publicar

        marca = uuid.uuid4().hex
        for i in range(3):
            await publicar(
                db_directa,
                aggregate_type="prueba",
                aggregate_id=uuid.uuid4(),
                event_type="prueba.metricas",
                dedup_key=f"metricas-{marca}-{i}",
            )
        await db_directa.commit()

        try:
            metrics.reiniciar_cache()
            await metrics.refrescar_indicadores(engine=motor_de_prueba)

            assert metrics.outbox_pendiente._value.get() >= 3
        finally:
            await db_directa.execute(
                text("DELETE FROM outbox_events WHERE dedup_key LIKE :p"),
                {"p": f"metricas-{marca}-%"},
            )
            await db_directa.commit()

    async def test_un_evento_agotado_se_cuenta_aparte(
        self, db_directa: AsyncSession, motor_de_prueba
    ) -> None:
        """`FAILED` alimenta su propia alerta: ya no se reintenta solo."""
        from app.modules.audit.outbox import publicar

        clave = f"agotado-{uuid.uuid4().hex}"
        evento_id = await publicar(
            db_directa,
            aggregate_type="prueba",
            aggregate_id=uuid.uuid4(),
            event_type="prueba.agotada",
            dedup_key=clave,
        )
        await db_directa.execute(
            text("""
                UPDATE outbox_events
                SET status = 'FAILED', processed_at = now(), attempt_count = 6
                WHERE id = :id
            """),
            {"id": evento_id},
        )
        await db_directa.commit()

        try:
            metrics.reiniciar_cache()
            await metrics.refrescar_indicadores(engine=motor_de_prueba)

            assert metrics.outbox_agotado._value.get() >= 1
        finally:
            await db_directa.execute(
                text("DELETE FROM outbox_events WHERE dedup_key = :k"), {"k": clave}
            )
            await db_directa.commit()

    async def test_la_cache_evita_consultar_en_cada_raspado(
        self, db_directa: AsyncSession, motor_de_prueba
    ) -> None:
        """Varias réplicas raspadas cada 15 s no deben ser una consulta por segundo.

        Se comprueba por comportamiento y no mirando el histograma: se inserta
        un evento ENTRE los dos refrescos, y si el segundo consultara la base,
        el indicador subiría.
        """
        from app.modules.audit.outbox import publicar

        clave = f"cache-{uuid.uuid4().hex}"
        metrics.reiniciar_cache()
        await metrics.refrescar_indicadores(engine=motor_de_prueba)
        antes = metrics.outbox_pendiente._value.get()

        try:
            await publicar(
                db_directa,
                aggregate_type="prueba",
                aggregate_id=uuid.uuid4(),
                event_type="prueba.cache",
                dedup_key=clave,
            )
            await db_directa.commit()

            await metrics.refrescar_indicadores(engine=motor_de_prueba)
            assert metrics.outbox_pendiente._value.get() == antes

            # Con `forzar` sí se entera: la caché retrasa el dato, no lo pierde.
            await metrics.refrescar_indicadores(engine=motor_de_prueba, forzar=True)
            assert metrics.outbox_pendiente._value.get() == antes + 1
        finally:
            await db_directa.execute(
                text("DELETE FROM outbox_events WHERE dedup_key = :k"), {"k": clave}
            )
            await db_directa.commit()

    async def test_una_base_caida_no_tumba_el_raspado(self, monkeypatch) -> None:
        """Si `/metrics` fallara, se perdería todo lo demás justo cuando hace falta."""

        def explota():
            raise RuntimeError("base caída")

        monkeypatch.setattr("app.core.observability.metrics.get_engine", explota)
        metrics.reiniciar_cache()

        await metrics.refrescar_indicadores()  # no lanza


class TestContadores:
    async def test_un_login_fallido_se_cuenta_con_su_motivo(
        self, cliente: httpx.AsyncClient
    ) -> None:
        antes = metrics.login_fallido_total.labels(motivo="usuario_inexistente")._value.get()

        await cliente.post(
            "/api/v1/auth/login",
            json={"email": "no-existe@pruebas.amvarmar.com", "password": "loquesea-12345"},
        )

        despues = metrics.login_fallido_total.labels(motivo="usuario_inexistente")._value.get()
        assert despues == antes + 1

    async def test_las_peticiones_http_se_miden_por_plantilla_de_ruta(
        self, cliente: httpx.AsyncClient
    ) -> None:
        """Con la URL concreta, cada carga sería una serie temporal distinta."""
        await cliente.get(f"/api/v1/shipments/{uuid.uuid4()}")
        await cliente.get(f"/api/v1/shipments/{uuid.uuid4()}")

        muestras = {
            m.labels["ruta"]
            for metrica in metrics.REGISTRO.collect()
            if metrica.name == "amvarmar_http_peticion_duracion_segundos"
            for m in metrica.samples
        }

        assert "/api/v1/shipments/{shipment_id}" in muestras

    async def test_una_ruta_inexistente_no_crea_series_nuevas(
        self, cliente: httpx.AsyncClient
    ) -> None:
        """Si no, cualquiera podría inflar la memoria de Prometheus a pedido."""
        for _ in range(3):
            await cliente.get(f"/no-existe-{uuid.uuid4().hex}")

        rutas = {
            m.labels["ruta"]
            for metrica in metrics.REGISTRO.collect()
            if metrica.name == "amvarmar_http_peticion"
            for m in metrica.samples
        }

        assert not any(r.startswith("/no-existe-") for r in rutas)
        assert "desconocida" in rutas

    async def test_el_raspado_no_se_mide_a_si_mismo(
        self, cliente: httpx.AsyncClient, con_token: str
    ) -> None:
        """Cada raspado inflaría los percentiles con tráfico que no es real."""
        await cliente.get("/metrics", headers={"Authorization": f"Bearer {con_token}"})

        rutas = {
            m.labels["ruta"]
            for metrica in metrics.REGISTRO.collect()
            if metrica.name == "amvarmar_http_peticion"
            for m in metrica.samples
        }

        assert "/metrics" not in rutas

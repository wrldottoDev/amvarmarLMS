"""Endpoints de lectura de cargas y dashboard por HTTP (Paso 2.5)."""

import uuid

import pytest
from httpx import AsyncClient
from scripts.seed_document_types import sembrar as sembrar_documentos
from scripts.seed_rbac import sembrar as sembrar_rbac
from scripts.seed_shipment_statuses import sembrar as sembrar_estados
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import LIMITE_MAXIMO
from app.core.security.argon2 import hash_password
from tests.piezas import sembrar_pieza

pytestmark = pytest.mark.integration

PASSWORD = "una passphrase de prueba suficientemente larga"


@pytest.fixture
async def entorno(db_directa: AsyncSession):
    """Un cliente con cargas propias y otra empresa con las suyas."""
    await sembrar_rbac(db_directa)
    await sembrar_estados(db_directa)
    await sembrar_documentos(db_directa)

    marca = uuid.uuid4().hex[:8]
    email = f"http-{marca}@pruebas.amvarmar.com"

    empresa = (
        await db_directa.execute(
            text("INSERT INTO companies (legal_name, status) VALUES (:n, 'ACTIVE') RETURNING id"),
            {"n": f"HTTP {marca} S.A."},
        )
    ).scalar_one()
    ajena = (
        await db_directa.execute(
            text("INSERT INTO companies (legal_name, status) VALUES (:n, 'ACTIVE') RETURNING id"),
            {"n": f"Ajena {marca} S.A."},
        )
    ).scalar_one()

    user_id = (
        await db_directa.execute(
            text("""
                INSERT INTO users (email, password_hash, first_name, last_name, status)
                VALUES (:e, :h, 'Ana', 'Cliente', 'ACTIVE') RETURNING id
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
        text("""
            INSERT INTO company_memberships (company_id, user_id, status)
            VALUES (:c, :u, 'ACTIVE')
        """),
        {"c": empresa, "u": user_id},
    )

    origen = await _ubicacion(db_directa, "US", "MIA", "Miami")
    destino = await _ubicacion(db_directa, "CR", "SJO", "San José")

    propias = [await _carga(db_directa, empresa, user_id, origen, destino) for _ in range(3)]
    ajena_id = await _carga(db_directa, ajena, user_id, origen, destino)
    await db_directa.commit()

    yield {
        "email": email,
        "empresa": empresa,
        "propias": propias,
        "ajena": ajena_id,
    }

    # Orden inverso a las dependencias: casi todo apunta a users y companies
    # con RESTRICT, que es justamente lo que impide borrados en cascada
    # silenciosos sobre datos de negocio.
    await db_directa.execute(
        text("DELETE FROM shipments WHERE company_id = ANY(:c)"), {"c": [empresa, ajena]}
    )
    await db_directa.execute(
        text("DELETE FROM company_memberships WHERE company_id = ANY(:c)"),
        {"c": [empresa, ajena]},
    )
    await db_directa.execute(text("DELETE FROM users WHERE email = :e"), {"e": email})
    await db_directa.execute(
        text("DELETE FROM companies WHERE id = ANY(:c)"), {"c": [empresa, ajena]}
    )
    await db_directa.commit()


async def _ubicacion(session: AsyncSession, pais: str, ciudad: str, nombre: str) -> uuid.UUID:
    return (
        await session.execute(
            text("""
                INSERT INTO locations (country_code, city_code, location_code, name)
                VALUES (:p, :c, :cod, :n)
                ON CONFLICT (location_code) DO UPDATE SET name = EXCLUDED.name
                RETURNING id
            """),
            {"p": pais, "c": ciudad, "cod": f"{pais}-{ciudad}", "n": nombre},
        )
    ).scalar_one()


async def _carga(session, empresa, user_id, origen, destino) -> uuid.UUID:
    carga = (
        await session.execute(
            text("""
                INSERT INTO shipments
                    (company_id, created_by, current_status_code,
                     origin_location_id, destination_location_id)
                VALUES (:c, :u, 'IN_TRANSIT', :o, :d) RETURNING id
            """),
            {"c": empresa, "u": user_id, "o": origen, "d": destino},
        )
    ).scalar_one()
    # Toda carga activa necesita al menos una pieza.
    await sembrar_pieza(session, carga)
    return carga


async def _autenticar(cliente: AsyncClient, email: str) -> dict[str, str]:
    respuesta = await cliente.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    return {"Authorization": f"Bearer {respuesta.json()['access_token']}"}


class TestListado:
    async def test_devuelve_solo_las_cargas_propias(
        self, cliente: AsyncClient, entorno: dict
    ) -> None:
        cabeceras = await _autenticar(cliente, entorno["email"])

        r = await cliente.get("/api/v1/shipments", headers=cabeceras)

        assert r.status_code == 200
        ids = {item["id"] for item in r.json()["items"]}
        assert ids == {str(i) for i in entorno["propias"]}
        assert str(entorno["ajena"]) not in ids

    async def test_estado_y_pendientes_son_campos_separados(
        self, cliente: AsyncClient, entorno: dict
    ) -> None:
        """La regla de interfaz del proyecto, en el contrato de la API."""
        cabeceras = await _autenticar(cliente, entorno["email"])

        item = (await cliente.get("/api/v1/shipments", headers=cabeceras)).json()["items"][0]

        assert item["status"] == "IN_TRANSIT"
        assert item["open_requirements_count"] == 0
        assert item["client_action_required_count"] == 0

    async def test_el_limite_se_recorta_al_maximo(
        self, cliente: AsyncClient, entorno: dict
    ) -> None:
        cabeceras = await _autenticar(cliente, entorno["email"])

        r = await cliente.get("/api/v1/shipments?limit=10000", headers=cabeceras)

        assert r.status_code == 200
        assert len(r.json()["items"]) <= LIMITE_MAXIMO

    async def test_un_cursor_basura_da_400_con_formato_estandar(
        self, cliente: AsyncClient, entorno: dict
    ) -> None:
        cabeceras = await _autenticar(cliente, entorno["email"])

        r = await cliente.get("/api/v1/shipments?cursor=basura!!", headers=cabeceras)

        assert r.status_code == 400
        assert r.json()["error"]["code"] == "CURSOR_INVALIDO"

    async def test_sin_token_da_401(self, cliente: AsyncClient) -> None:
        r = await cliente.get("/api/v1/shipments")

        assert r.status_code == 401

    async def test_pagina_con_cursor(self, cliente: AsyncClient, entorno: dict) -> None:
        cabeceras = await _autenticar(cliente, entorno["email"])

        primera = (await cliente.get("/api/v1/shipments?limit=2", headers=cabeceras)).json()
        assert primera["has_more"] is True

        segunda = (
            await cliente.get(
                f"/api/v1/shipments?limit=2&cursor={primera['next_cursor']}",
                headers=cabeceras,
            )
        ).json()

        vistos = [i["id"] for i in primera["items"]] + [i["id"] for i in segunda["items"]]
        assert len(vistos) == len(set(vistos)) == 3


class TestDetalleYTimeline:
    async def test_detalle_incluye_row_version(self, cliente: AsyncClient, entorno: dict) -> None:
        """El frontend lo necesita para poder hacer un PATCH."""
        cabeceras = await _autenticar(cliente, entorno["email"])

        r = await cliente.get(f"/api/v1/shipments/{entorno['propias'][0]}", headers=cabeceras)

        assert r.status_code == 200
        assert r.json()["row_version"] == 1

    async def test_una_carga_ajena_da_404(self, cliente: AsyncClient, entorno: dict) -> None:
        cabeceras = await _autenticar(cliente, entorno["email"])

        r = await cliente.get(f"/api/v1/shipments/{entorno['ajena']}", headers=cabeceras)

        assert r.status_code == 404
        assert r.json()["error"]["code"] == "RECURSO_NO_ENCONTRADO"

    async def test_el_timeline_de_una_carga_ajena_da_404(
        self, cliente: AsyncClient, entorno: dict
    ) -> None:
        cabeceras = await _autenticar(cliente, entorno["email"])

        r = await cliente.get(f"/api/v1/shipments/{entorno['ajena']}/timeline", headers=cabeceras)

        assert r.status_code == 404

    async def test_el_timeline_propio_responde(self, cliente: AsyncClient, entorno: dict) -> None:
        cabeceras = await _autenticar(cliente, entorno["email"])

        r = await cliente.get(
            f"/api/v1/shipments/{entorno['propias'][0]}/timeline", headers=cabeceras
        )

        assert r.status_code == 200
        assert r.json()["items"] == []


class TestDashboardHttp:
    async def test_el_dashboard_del_cliente_responde(
        self, cliente: AsyncClient, entorno: dict
    ) -> None:
        cabeceras = await _autenticar(cliente, entorno["email"])

        r = await cliente.get("/api/v1/dashboard/client", headers=cabeceras)

        assert r.status_code == 200
        cuerpo = r.json()
        # Solo cuenta las propias, no las de la otra empresa.
        assert cuerpo["tarjetas"]["en_transito"] == 3
        assert set(cuerpo["tarjetas"]) == {
            "en_bodega",
            "en_transito",
            "proximos_a_llegar",
            "requieren_accion",
            "entregados_este_mes",
        }

    async def test_los_movimientos_traen_estado_y_pendientes_por_separado(
        self, cliente: AsyncClient, entorno: dict
    ) -> None:
        cabeceras = await _autenticar(cliente, entorno["email"])

        cuerpo = (await cliente.get("/api/v1/dashboard/client", headers=cabeceras)).json()

        for movimiento in cuerpo["proximos_movimientos"]:
            assert "status" in movimiento
            assert "open_requirements_count" in movimiento

    async def test_sin_token_da_401(self, cliente: AsyncClient) -> None:
        r = await cliente.get("/api/v1/dashboard/client")

        assert r.status_code == 401

"""Bandeja de notificaciones por HTTP (Paso 4.2)."""

import uuid

import httpx
import pytest
from scripts.seed_rbac import sembrar as sembrar_rbac
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security.argon2 import hash_password
from app.modules.notifications import service

pytestmark = pytest.mark.integration

PASSWORD = "Contrasena-De-Pruebas-1"


@pytest.fixture
async def entorno(db_directa: AsyncSession):
    """Dos clientes de empresas distintas, cada uno con sus avisos."""
    await sembrar_rbac(db_directa)
    marca = uuid.uuid4().hex[:8]

    creados = {}
    for etiqueta in ("ana", "beto"):
        empresa = (
            await db_directa.execute(
                text(
                    "INSERT INTO companies (legal_name, status) VALUES (:n,'ACTIVE') RETURNING id"
                ),
                {"n": f"{etiqueta}-{marca} S.A."},
            )
        ).scalar_one()
        email = f"{etiqueta}-{marca}@pruebas.amvarmar.com"
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
        creados[etiqueta] = {"user_id": user_id, "email": email, "empresa": empresa}

        destinatario = service.Destinatario(
            user_id=user_id, email=None, email_verificado=False, company_id=empresa
        )
        for _ in range(3):
            await service.notificar(
                db_directa,
                event_code="shipment.status_changed",
                destinatarios=[destinatario],
                resource_type="shipment",
                resource_id=uuid.uuid4(),
                dedup_key=f"http-{uuid.uuid4().hex}",
            )

    await db_directa.commit()

    yield creados

    usuarios = [c["user_id"] for c in creados.values()]
    empresas = [c["empresa"] for c in creados.values()]
    await db_directa.execute(
        text("DELETE FROM notifications WHERE user_id = ANY(:u)"), {"u": usuarios}
    )
    # Esta prueba se autentica de verdad, así que deja sesiones y refresh
    # tokens committeados. Borrar los usuarios los arrastra por cascada; sin
    # esto, otras pruebas que consultan esas tablas encuentran filas ajenas.
    await db_directa.execute(
        text("DELETE FROM user_role_assignments WHERE user_id = ANY(:u)"), {"u": usuarios}
    )
    await db_directa.execute(text("DELETE FROM users WHERE id = ANY(:u)"), {"u": usuarios})
    await db_directa.execute(text("DELETE FROM companies WHERE id = ANY(:c)"), {"c": empresas})
    await db_directa.commit()


async def _autenticar(cliente: httpx.AsyncClient, email: str) -> dict[str, str]:
    respuesta = await cliente.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    return {"Authorization": f"Bearer {respuesta.json()['access_token']}"}


class TestListado:
    async def test_devuelve_solo_las_propias(
        self, cliente: httpx.AsyncClient, entorno: dict
    ) -> None:
        """El alcance es el `user_id` del token, no un parámetro del cliente."""
        cabeceras = await _autenticar(cliente, entorno["ana"]["email"])

        r = await cliente.get("/api/v1/notifications", headers=cabeceras)

        assert r.status_code == 200
        cuerpo = r.json()
        assert len(cuerpo["items"]) == 3
        assert cuerpo["unread_count"] == 3

    async def test_sin_token_da_401(self, cliente: httpx.AsyncClient, entorno: dict) -> None:
        assert (await cliente.get("/api/v1/notifications")).status_code == 401

    async def test_pagina_con_cursor(self, cliente: httpx.AsyncClient, entorno: dict) -> None:
        cabeceras = await _autenticar(cliente, entorno["ana"]["email"])

        primera = (await cliente.get("/api/v1/notifications?limit=2", headers=cabeceras)).json()
        assert primera["has_more"]

        segunda = (
            await cliente.get(
                f"/api/v1/notifications?limit=2&cursor={primera['next_cursor']}",
                headers=cabeceras,
            )
        ).json()

        ids_primera = {n["id"] for n in primera["items"]}
        ids_segunda = {n["id"] for n in segunda["items"]}
        assert ids_primera & ids_segunda == set()

    async def test_cursor_invalido_da_error_claro(
        self, cliente: httpx.AsyncClient, entorno: dict
    ) -> None:
        cabeceras = await _autenticar(cliente, entorno["ana"]["email"])

        r = await cliente.get("/api/v1/notifications?cursor=basura", headers=cabeceras)

        # 400, igual que el listado de cargas: un cursor mal formado es un
        # dato inválido del cliente, no un fallo de validación de esquema.
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "CURSOR_INVALIDO"


class TestMarcarLeidas:
    async def test_marcar_una_baja_el_contador(
        self, cliente: httpx.AsyncClient, entorno: dict
    ) -> None:
        cabeceras = await _autenticar(cliente, entorno["ana"]["email"])
        listado = (await cliente.get("/api/v1/notifications", headers=cabeceras)).json()
        objetivo = listado["items"][0]["id"]

        r = await cliente.post(f"/api/v1/notifications/{objetivo}/read", headers=cabeceras)

        assert r.status_code == 200
        assert r.json()["unread_count"] == 2

    async def test_no_se_marca_la_de_otra_persona(
        self, cliente: httpx.AsyncClient, entorno: dict
    ) -> None:
        """404, no 403: confirmar que existe ya diría algo de la otra persona."""
        de_ana = (
            await cliente.get(
                "/api/v1/notifications",
                headers=await _autenticar(cliente, entorno["ana"]["email"]),
            )
        ).json()["items"][0]["id"]

        cabeceras_beto = await _autenticar(cliente, entorno["beto"]["email"])
        r = await cliente.post(f"/api/v1/notifications/{de_ana}/read", headers=cabeceras_beto)

        assert r.status_code == 404
        assert (await cliente.get("/api/v1/notifications", headers=cabeceras_beto)).json()[
            "unread_count"
        ] == 3

    async def test_marcar_todas(self, cliente: httpx.AsyncClient, entorno: dict) -> None:
        cabeceras = await _autenticar(cliente, entorno["ana"]["email"])

        r = await cliente.post("/api/v1/notifications/read-all", headers=cabeceras)

        assert r.status_code == 200
        assert r.json() == {"marcadas": 3, "unread_count": 0}
        vacio = (await cliente.get("/api/v1/notifications?unread=true", headers=cabeceras)).json()
        assert vacio["items"] == []

    async def test_marcar_dos_veces_no_baja_de_mas(
        self, cliente: httpx.AsyncClient, entorno: dict
    ) -> None:
        cabeceras = await _autenticar(cliente, entorno["ana"]["email"])
        objetivo = (await cliente.get("/api/v1/notifications", headers=cabeceras)).json()["items"][
            0
        ]["id"]

        await cliente.post(f"/api/v1/notifications/{objetivo}/read", headers=cabeceras)
        segunda = await cliente.post(f"/api/v1/notifications/{objetivo}/read", headers=cabeceras)

        assert segunda.status_code == 200
        assert segunda.json()["unread_count"] == 2

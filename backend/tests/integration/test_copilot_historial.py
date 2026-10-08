"""Historial de AMVI, calificaciones y aprendizaje (ADR-0012, enmienda 2026-10-08)."""

import json
import re
import uuid

import pytest
from httpx import AsyncClient
from scripts.seed_rbac import sembrar as sembrar_rbac
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.copilot import executors, historial
from app.modules.rbac.service import obtener_permisos_efectivos
from tests.integration.test_copilot_router import (
    _autenticar,
    _clave_openai,
    _ProveedorFalso,
    _texto,
    _usuario_cliente,
    _usuario_interno,
)

pytestmark = pytest.mark.integration


async def _preguntar(cliente: AsyncClient, headers: dict, conversacion: str, pregunta: str) -> dict:
    """Un turno completo; devuelve los datos del evento `guardado`."""
    with _clave_openai("sk-test-no-se-usa"):
        resp = await cliente.post(
            "/api/v1/copilot/respond",
            headers=headers,
            json={
                "mensajes": [{"rol": "user", "contenido": pregunta}],
                "conversacion_id": conversacion,
            },
        )
    assert resp.status_code == 200
    guardado = re.search(r"event: guardado\ndata: (.+)\n", resp.text)
    assert guardado, resp.text
    datos: dict = json.loads(guardado.group(1))
    return datos


class TestHistorial:
    async def test_el_turno_queda_guardado_y_se_retoma(
        self, cliente: AsyncClient, db_directa: AsyncSession, usar_proveedor
    ) -> None:
        await sembrar_rbac(db_directa)
        _actor, email, _empresa = await _usuario_cliente(db_directa)
        await db_directa.commit()
        headers = await _autenticar(cliente, email)
        usar_proveedor(_ProveedorFalso(guion=[_texto("Tenés 3 cargas en Miami.")]))

        guardado = await _preguntar(cliente, headers, "conv-historial", "¿Cuántas cargas tengo?")

        lista = (await cliente.get("/api/v1/copilot/conversations", headers=headers)).json()
        assert [c["title"] for c in lista] == ["¿Cuántas cargas tengo?"]
        assert lista[0]["client_key"] == "conv-historial"  # pragma: allowlist secret
        mensajes = (
            await cliente.get(f"/api/v1/copilot/conversations/{lista[0]['id']}", headers=headers)
        ).json()
        assert [(m["role"], m["content"]) for m in mensajes] == [
            ("user", "¿Cuántas cargas tengo?"),
            ("assistant", "Tenés 3 cargas en Miami."),
        ]
        assert mensajes[1]["id"] == guardado["mensaje_id"]

    async def test_una_conversacion_ajena_no_existe(
        self, cliente: AsyncClient, db_directa: AsyncSession, usar_proveedor
    ) -> None:
        await sembrar_rbac(db_directa)
        _a, email_a, _ = await _usuario_cliente(db_directa)
        _b, email_b, _ = await _usuario_cliente(db_directa)
        await db_directa.commit()
        headers_a = await _autenticar(cliente, email_a)
        headers_b = await _autenticar(cliente, email_b)
        usar_proveedor(_ProveedorFalso(guion=[_texto("Hola.")]))
        guardado = await _preguntar(cliente, headers_a, "conv-ajena", "hola")

        ver = await cliente.get(
            f"/api/v1/copilot/conversations/{guardado['conversacion_id']}", headers=headers_b
        )
        calificar = await cliente.post(
            f"/api/v1/copilot/messages/{guardado['mensaje_id']}/feedback",
            headers=headers_b,
            json={"valor": -1},
        )
        borrar = await cliente.delete(
            f"/api/v1/copilot/conversations/{guardado['conversacion_id']}", headers=headers_b
        )

        assert (ver.status_code, calificar.status_code, borrar.status_code) == (404, 404, 404)
        assert (await cliente.get("/api/v1/copilot/conversations", headers=headers_b)).json() == []

    async def test_se_purga_a_los_180_dias_sin_uso(self, db_directa: AsyncSession) -> None:
        await sembrar_rbac(db_directa)
        actor, _email = await _usuario_interno(db_directa)
        vieja = await historial.guardar_pregunta(
            db_directa, user_id=actor, company_id=None, client_key="vieja", texto="hola"
        )
        await historial.guardar_pregunta(
            db_directa, user_id=actor, company_id=None, client_key="nueva", texto="hola"
        )
        await db_directa.execute(
            text(
                "UPDATE copilot_conversations SET updated_at = now() - interval '181 days' "
                "WHERE id = :c"
            ),
            {"c": vieja},
        )

        assert await historial.purgar(db_directa) == 1
        restantes = await historial.listar(db_directa, user_id=actor)
        assert [c.client_key for c in restantes] == ["nueva"]


class TestAprendizaje:
    async def test_lo_mal_calificado_llega_a_operaciones_con_la_pregunta(
        self, cliente: AsyncClient, db_directa: AsyncSession, usar_proveedor
    ) -> None:
        await sembrar_rbac(db_directa)
        _cli, email_cliente, _ = await _usuario_cliente(db_directa)
        _ops, email_ops = await _usuario_interno(db_directa)
        await db_directa.commit()
        headers_cliente = await _autenticar(cliente, email_cliente)
        headers_ops = await _autenticar(cliente, email_ops)
        usar_proveedor(_ProveedorFalso(guion=[_texto("No sé.")]))
        pregunta = f"¿Cuánto cuesta el bodegaje? {uuid.uuid4().hex[:6]}"
        guardado = await _preguntar(cliente, headers_cliente, "conv-mala", pregunta)

        calificar = await cliente.post(
            f"/api/v1/copilot/messages/{guardado['mensaje_id']}/feedback",
            headers=headers_cliente,
            json={"valor": -1, "comentario": "No me ayudó"},
        )
        cliente_ve = await cliente.get("/api/v1/copilot/learning", headers=headers_cliente)
        aprendizaje = (await cliente.get("/api/v1/copilot/learning", headers=headers_ops)).json()

        assert calificar.status_code == 204
        assert cliente_ve.status_code == 403
        mala = next(m for m in aprendizaje["mal_calificadas"] if m["pregunta"] == pregunta)
        assert (mala["respuesta"], mala["comentario"]) == ("No sé.", "No me ayudó")

    async def test_una_guia_nueva_contesta_lo_que_antes_no(
        self, db_directa: AsyncSession, redis
    ) -> None:
        await sembrar_rbac(db_directa)
        actor, _email = await _usuario_interno(db_directa)
        permisos = await obtener_permisos_efectivos(db_directa, redis, actor)
        # Palabras que ninguna guía de los archivos usa.
        tema = {"tema": "zorzal pomelo kiwi"}

        antes = await executors.como_hago(db_directa, permisos, actor, None, tema)
        sin_guia = (
            await db_directa.execute(
                text("SELECT count(*) FROM copilot_unanswered_topics WHERE topic = :t"),
                {"t": tema["tema"]},
            )
        ).scalar_one()
        await historial.crear_guia(
            db_directa,
            titulo="Horario de la bodega Zorzal",
            palabras_clave="zorzal, pomelo",
            contenido="La bodega Zorzal atiende de lunes a viernes de 8:00 a 17:00.",
            actor=actor,
        )
        despues = await executors.como_hago(db_directa, permisos, actor, None, tema)

        assert antes["tiene_respuesta"] is False
        assert sin_guia == 1
        assert despues["tiene_respuesta"] is True
        assert despues["titulo"] == "Horario de la bodega Zorzal"

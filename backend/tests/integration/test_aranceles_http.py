"""Calculadora de impuestos de prueba (estimador, fase F1): solo AMVARMAR."""

import pytest
from httpx import AsyncClient
from scripts.seed_rbac import sembrar as sembrar_rbac
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.test_copilot_router import _autenticar, _usuario_cliente, _usuario_interno

pytestmark = pytest.mark.integration

URL = "/api/v1/aranceles/estimar"
EJEMPLO_DGA = {"valor_mercancia_usd": "9600", "tipo_cambio": "602", "dai": "14"}


async def test_admin_obtiene_el_desglose_del_ejemplo_oficial(
    cliente: AsyncClient, db_directa: AsyncSession
) -> None:
    await sembrar_rbac(db_directa)
    _actor, email = await _usuario_interno(db_directa)
    await db_directa.commit()

    respuesta = await cliente.post(URL, json=EJEMPLO_DGA, headers=await _autenticar(cliente, email))

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["total"] == "1730870.40"
    assert {linea["concepto"]: linea["monto"] for linea in cuerpo["lineas"]} == {
        "DAI": "809088.00",
        "Ley 6946": "57792.00",
        "IVA": "863990.40",
    }


async def test_un_cliente_no_la_usa(cliente: AsyncClient, db_directa: AsyncSession) -> None:
    await sembrar_rbac(db_directa)
    _actor, email, _empresa = await _usuario_cliente(db_directa)
    await db_directa.commit()

    respuesta = await cliente.post(URL, json=EJEMPLO_DGA, headers=await _autenticar(cliente, email))

    assert respuesta.status_code == 403


async def test_sin_sesion_no_responde(cliente: AsyncClient) -> None:
    assert (await cliente.post(URL, json=EJEMPLO_DGA)).status_code == 401


async def test_rechaza_valores_imposibles(cliente: AsyncClient, db_directa: AsyncSession) -> None:
    await sembrar_rbac(db_directa)
    _actor, email = await _usuario_interno(db_directa)
    await db_directa.commit()
    cabeceras = await _autenticar(cliente, email)

    sin_valor = await cliente.post(
        URL, json=EJEMPLO_DGA | {"valor_mercancia_usd": "0"}, headers=cabeceras
    )
    tasa_negativa = await cliente.post(URL, json=EJEMPLO_DGA | {"dai": "-1"}, headers=cabeceras)

    assert (sin_valor.status_code, tasa_negativa.status_code) == (422, 422)

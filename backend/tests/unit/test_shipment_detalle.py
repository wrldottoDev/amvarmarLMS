"""Regresión: el detalle debe abrir tanto WR como cargas de tránsito."""

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.shipments import router


@pytest.mark.parametrize("volumen", [None, Decimal("2.750")])
async def test_detalle_conserva_volumen_sin_argumentos_duplicados(monkeypatch, volumen):
    ahora = datetime.now(UTC)
    fila = SimpleNamespace(
        id=uuid4(),
        shipment_number="TEST-001",
        company_id=uuid4(),
        company_name="Empresa de prueba",
        row_version=1,
        current_status_code="RECEIVED",
        requisitos_abiertos=0,
        requisitos_del_cliente=0,
        factura=None,
        wr=None,
        tracking=None,
        po=None,
        contenedor=None,
        shipper=None,
        carrier=None,
        foots_cft=None,
        volume_m3=volumen,
        weight_kg=None,
        weight_lb=None,
        weight_source_unit=None,
        hidden_at=None,
        archived_at=None,
        origen_id=uuid4(),
        origen_codigo="ORIGIN",
        origen_nombre="Origen",
        origen_pais="US",
        destino_id=uuid4(),
        destino_codigo="DEST",
        destino_nombre="Destino",
        destino_pais="CR",
        estimated_arrival_at=None,
        current_location=None,
        transport_mode=None,
        package_count=1,
        permit_review_required=False,
        created_at=ahora,
        updated_at=ahora,
        description=None,
        destination_address=None,
        volumetric_weight_kg=None,
        received_at=ahora,
        stored_at=None,
        dispatched_at=None,
        delivered_at=None,
    )
    monkeypatch.setattr(router, "obtener_permisos_efectivos", AsyncMock())
    monkeypatch.setattr(router.queries, "obtener_shipment", AsyncMock(return_value=fila))
    monkeypatch.setattr(router.queries, "bultos", AsyncMock(return_value=[]))

    detalle = await router.obtener_shipment(
        shipment_id=fila.id,
        actor=SimpleNamespace(user_id=uuid4()),
        db=AsyncMock(),
        redis=AsyncMock(),
    )

    assert detalle.id == fila.id
    assert detalle.volume_m3 == volumen
    assert detalle.packages == []
    assert detalle.received_at == ahora

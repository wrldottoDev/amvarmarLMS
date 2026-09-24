"""AMVI prepara solicitudes de despacho (ADR-0012 y ADR-0017, enmienda 2026-09-24).

El cliente nombra sus cargas por número, factura o ID; AMVI arma la solicitud
y el cliente la confirma. La confirmación crea la solicitud con
`dispatches.service.crear`, el mismo camino que `POST /dispatches`: nada de un
INSERT propio.
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ErrorDeAplicacion
from app.modules.copilot import confirmaciones, executors_escritura
from app.modules.copilot.router import puede_adjuntar
from app.modules.copilot.tools import puede_ejecutar
from tests.integration.test_copilot_escritura import _entorno, _permisos, _propuesta
from tests.piezas import sembrar_pieza

pytestmark = pytest.mark.integration


async def _carga(
    session: AsyncSession,
    ctx: dict,
    *,
    estado: str = "STORED",
    empresa: str = "empresa_a",
    factura: str | None = None,
) -> tuple[uuid.UUID, str]:
    fila = (
        await session.execute(
            text("""
                INSERT INTO shipments
                    (company_id, created_by, current_status_code,
                     origin_location_id, destination_location_id)
                VALUES (:c, :u, :e, :o, :d)
                RETURNING id, shipment_number
            """),
            {
                "c": ctx[empresa],
                "u": ctx["operaciones"],
                "e": estado,
                "o": ctx["origen"],
                "d": ctx["destino"],
            },
        )
    ).one()
    await sembrar_pieza(session, fila.id)
    if factura:
        await session.execute(
            text("""
                INSERT INTO shipment_references (shipment_id, reference_type, value)
                VALUES (:s, 'INVOICE', :v)
            """),
            {"s": fila.id, "v": factura},
        )
    return fila.id, fila.shipment_number


async def _proponer(session, redis, ctx, cargas: list[str], **extra) -> dict:
    actor = extra.pop("actor", "cliente_a")
    permisos = await _permisos(session, redis, ctx[actor])
    return await executors_escritura.proponer_despacho(
        session,
        permisos,
        ctx[actor],
        None,
        {"cargas": cargas, "metodo": extra.pop("metodo", "SEA"), **extra},
    )


async def _solicitudes(session: AsyncSession) -> int:
    return int((await session.execute(text("SELECT count(*) FROM dispatch_requests"))).scalar_one())


class TestPermisos:
    async def test_el_cliente_tiene_la_herramienta_y_solo_esa(
        self, session: AsyncSession, redis
    ) -> None:
        """Recupera `copilot.tools.draft`, pero las demás acciones siguen
        exigiendo permisos de AMVARMAR que el cliente no tiene."""
        ctx = await _entorno(session)
        permisos = await _permisos(session, redis, ctx["cliente_a"])

        assert puede_ejecutar("proponer_despacho", permisos) is True
        for otra in ("crear_prealerta_borrador", "procesar_factura_ocr", "proponer_cambio_estado"):
            assert puede_ejecutar(otra, permisos) is False

    async def test_el_cliente_sigue_sin_poder_adjuntar(self, session: AsyncSession, redis) -> None:
        """El adjunto solo alimenta el alta de cargas; al cliente le costaría una
        lectura OCR que no le sirve para nada."""
        ctx = await _entorno(session)

        assert puede_adjuntar(await _permisos(session, redis, ctx["cliente_a"])) is False
        assert puede_adjuntar(await _permisos(session, redis, ctx["operaciones"])) is True


class TestPropuesta:
    async def test_arma_la_solicitud_sin_crear_nada(self, session: AsyncSession, redis) -> None:
        ctx = await _entorno(session)
        factura = f"FAC-{uuid.uuid4().hex[:8]}"
        primera, numero = await _carga(session, ctx)
        segunda, _ = await _carga(session, ctx, factura=factura)
        antes = await _solicitudes(session)

        resultado = await _proponer(
            session, redis, ctx, [numero, factura], direccion_entrega="Bodega central, Heredia"
        )

        assert resultado["action_code"] == "proponer_despacho"
        propuesta = await _propuesta(session, uuid.UUID(resultado["id"]))
        assert propuesta.status == "PENDING"
        assert set(propuesta.payload["shipment_ids"]) == {str(primera), str(segunda)}
        assert propuesta.payload["method"] == "SEA"
        assert await _solicitudes(session) == antes

    async def test_una_carga_no_almacenada_no_se_propone(
        self, session: AsyncSession, redis
    ) -> None:
        ctx = await _entorno(session)
        _, lista = await _carga(session, ctx)
        _, en_transito = await _carga(session, ctx, estado="IN_TRANSIT")

        resultado = await _proponer(session, redis, ctx, [lista, en_transito])

        assert resultado["propuesta"] is None
        assert en_transito in resultado["motivo"]

    async def test_una_carga_de_otra_empresa_no_existe_para_el_cliente(
        self, session: AsyncSession, redis
    ) -> None:
        ctx = await _entorno(session)
        _, ajena = await _carga(session, ctx, empresa="empresa_b")

        resultado = await _proponer(session, redis, ctx, [ajena])

        assert resultado["propuesta"] is None
        assert ajena in resultado["no_encontradas"]

    async def test_una_factura_repetida_pide_elegir(self, session: AsyncSession, redis) -> None:
        ctx = await _entorno(session)
        factura = f"FAC-{uuid.uuid4().hex[:8]}"
        await _carga(session, ctx, factura=factura)
        await _carga(session, ctx, factura=factura)

        resultado = await _proponer(session, redis, ctx, [factura])

        assert resultado["propuesta"] is None
        assert factura in resultado["ambiguas"]

    async def test_avisa_si_hay_requisitos_pendientes_pero_propone(
        self, session: AsyncSession, redis
    ) -> None:
        """El cliente puede pedirlo; Operaciones lo aprueba cuando se resuelve."""
        ctx = await _entorno(session)
        carga, numero = await _carga(session, ctx)
        await session.execute(
            text("""
                INSERT INTO shipment_requirements
                    (shipment_id, requirement_type, title, required_from, status,
                     blocks_dispatch, created_by)
                VALUES (:s, 'ACTION', 'Pago de bodegaje', 'CLIENT', 'OPEN', true, :u)
            """),
            {"s": carga, "u": ctx["operaciones"]},
        )

        resultado = await _proponer(session, redis, ctx, [numero])

        assert resultado["action_code"] == "proponer_despacho"
        assert any("Pago de bodegaje" in a for a in resultado["advertencias"])

    async def test_operaciones_no_mezcla_empresas(self, session: AsyncSession, redis) -> None:
        ctx = await _entorno(session)
        _, de_a = await _carga(session, ctx)
        _, de_b = await _carga(session, ctx, empresa="empresa_b")

        resultado = await _proponer(session, redis, ctx, [de_a, de_b], actor="operaciones")

        assert resultado["propuesta"] is None
        assert "empresa" in resultado["motivo"]


class TestConfirmacion:
    async def test_confirmar_crea_la_solicitud_con_el_servicio_de_despachos(
        self, session: AsyncSession, redis
    ) -> None:
        ctx = await _entorno(session)
        carga, numero = await _carga(session, ctx)
        resultado = await _proponer(session, redis, ctx, [numero])
        permisos = await _permisos(session, redis, ctx["cliente_a"])

        confirmado = await confirmaciones.confirmar_proponer_despacho(
            session,
            permisos,
            uuid.UUID(resultado["id"]),
            {"instrucciones": "Llamar antes de llegar"},
        )

        fila = (
            await session.execute(
                text("""
                    SELECT d.status, d.method, d.instructions, d.company_id,
                           s.current_status_code
                    FROM dispatch_requests d
                    JOIN dispatch_request_shipments ds ON ds.dispatch_request_id = d.id
                    JOIN shipments s ON s.id = ds.shipment_id
                    WHERE d.id = :d
                """),
                {"d": uuid.UUID(confirmado["dispatch_id"])},
            )
        ).one()
        assert fila.status == "PENDING"
        assert fila.method == "SEA"
        assert fila.instructions == "Llamar antes de llegar"
        assert fila.company_id == ctx["empresa_a"]
        assert fila.current_status_code == "DISPATCH_REQUESTED"
        assert confirmado["dispatch_number"]
        eventos = (
            await session.execute(
                text("""
                    SELECT count(*) FROM outbox_events
                    WHERE aggregate_id = :d AND event_type = 'dispatch.created'
                """),
                {"d": uuid.UUID(confirmado["dispatch_id"])},
            )
        ).scalar_one()
        assert eventos == 1
        assert carga

    async def test_los_campos_no_cambian_empresa_ni_cargas(
        self, session: AsyncSession, redis
    ) -> None:
        ctx = await _entorno(session)
        carga, numero = await _carga(session, ctx)
        _, ajena = await _carga(session, ctx, empresa="empresa_b")
        resultado = await _proponer(session, redis, ctx, [numero])
        permisos = await _permisos(session, redis, ctx["cliente_a"])

        confirmado = await confirmaciones.confirmar_proponer_despacho(
            session,
            permisos,
            uuid.UUID(resultado["id"]),
            {"company_id": str(ctx["empresa_b"]), "cargas": ajena, "shipment_ids": "x"},
        )

        cargas = (
            await session.execute(
                text("""
                    SELECT shipment_id FROM dispatch_request_shipments
                    WHERE dispatch_request_id = :d
                """),
                {"d": uuid.UUID(confirmado["dispatch_id"])},
            )
        ).scalars()
        assert list(cargas) == [carga]

    async def test_si_la_carga_ya_no_esta_almacenada_no_crea_nada(
        self, session: AsyncSession, redis
    ) -> None:
        ctx = await _entorno(session)
        carga, numero = await _carga(session, ctx)
        resultado = await _proponer(session, redis, ctx, [numero])
        await session.execute(
            text("UPDATE shipments SET current_status_code = 'RECEIVED' WHERE id = :s"),
            {"s": carga},
        )
        antes = await _solicitudes(session)
        permisos = await _permisos(session, redis, ctx["cliente_a"])

        with pytest.raises(ErrorDeAplicacion):
            await confirmaciones.confirmar_proponer_despacho(
                session, permisos, uuid.UUID(resultado["id"]), {}
            )

        assert await _solicitudes(session) == antes

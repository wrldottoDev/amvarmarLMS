"""AMVI cambia estados de ingreso a bodega, con confirmación (ADR-0012).

`proponer_cambio_estado` solo cubre Prealerta → En tránsito → Recibida →
Almacenada: los avances que no piden justificación. La carga se identifica
por número, factura o ID, y tiene que ser UNA coincidencia exacta: con una
búsqueda parcial, "SHP-2026-00" movería la carga equivocada. La confirmación
pasa por el mismo motor de transiciones que la API.
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ErrorDeAplicacion, SinPermiso
from app.modules.copilot import confirmaciones, executors_escritura
from app.modules.copilot.tools import puede_ejecutar
from app.modules.shipments import service as cargas
from tests.integration.test_copilot_escritura import _entorno, _permisos, _propuesta
from tests.piezas import sembrar_pieza

pytestmark = pytest.mark.integration


async def _carga(
    session: AsyncSession, ctx: dict, estado: str, *, factura: str | None = None
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
                "c": ctx["empresa_a"],
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


async def _estado(session: AsyncSession, shipment_id: uuid.UUID) -> str:
    return str(
        (
            await session.execute(
                text("SELECT current_status_code FROM shipments WHERE id = :id"),
                {"id": shipment_id},
            )
        ).scalar_one()
    )


async def _proponer(session, redis, ctx, carga: str, destino: str) -> dict:
    permisos = await _permisos(session, redis, ctx["operaciones"])
    return await executors_escritura.proponer_cambio_estado(
        session, permisos, ctx["operaciones"], None, {"carga": carga, "estado_destino": destino}
    )


class TestPropuesta:
    async def test_por_numero_de_carga_propone_sin_tocar_la_carga(
        self, session: AsyncSession, redis
    ) -> None:
        ctx = await _entorno(session)
        carga, numero = await _carga(session, ctx, "IN_TRANSIT")

        resultado = await _proponer(session, redis, ctx, numero, "RECEIVED")

        assert resultado["action_code"] == "proponer_cambio_estado"
        propuesta = await _propuesta(session, uuid.UUID(resultado["id"]))
        assert propuesta.status == "PENDING"
        assert propuesta.payload["pasos"] == ["RECEIVED"]
        assert propuesta.payload["shipment_id"] == str(carga)
        assert await _estado(session, carga) == "IN_TRANSIT"

    async def test_por_numero_de_factura(self, session: AsyncSession, redis) -> None:
        ctx = await _entorno(session)
        factura = f"FAC-{uuid.uuid4().hex[:8]}"
        carga, _ = await _carga(session, ctx, "PRE_ALERT", factura=factura)

        resultado = await _proponer(session, redis, ctx, factura.lower(), "IN_TRANSIT")

        propuesta = await _propuesta(session, uuid.UUID(resultado["id"]))
        assert propuesta.payload["shipment_id"] == str(carga)

    async def test_por_id(self, session: AsyncSession, redis) -> None:
        ctx = await _entorno(session)
        carga, _ = await _carga(session, ctx, "RECEIVED")

        resultado = await _proponer(session, redis, ctx, str(carga), "STORED")

        propuesta = await _propuesta(session, uuid.UUID(resultado["id"]))
        assert propuesta.payload["pasos"] == ["STORED"]

    async def test_de_prealerta_a_almacenada_arma_los_tres_pasos(
        self, session: AsyncSession, redis
    ) -> None:
        """El motor avanza de a un estado; la propuesta los encadena en orden."""
        ctx = await _entorno(session)
        _carga_id, numero = await _carga(session, ctx, "PRE_ALERT")

        resultado = await _proponer(session, redis, ctx, numero, "STORED")

        propuesta = await _propuesta(session, uuid.UUID(resultado["id"]))
        assert propuesta.payload["pasos"] == ["IN_TRANSIT", "RECEIVED", "STORED"]

    async def test_una_factura_repetida_pide_elegir_sin_proponer(
        self, session: AsyncSession, redis
    ) -> None:
        ctx = await _entorno(session)
        factura = f"FAC-{uuid.uuid4().hex[:8]}"
        await _carga(session, ctx, "PRE_ALERT", factura=factura)
        await _carga(session, ctx, "IN_TRANSIT", factura=factura)
        antes = await _cuenta_propuestas(session)

        resultado = await _proponer(session, redis, ctx, factura, "RECEIVED")

        assert resultado["ambigua"] is True
        assert len(resultado["candidatas"]) == 2
        assert await _cuenta_propuestas(session) == antes

    async def test_una_coincidencia_parcial_no_cuenta(self, session: AsyncSession, redis) -> None:
        """Con ILIKE, 'SHP-2026' encontraría cualquier carga del año."""
        ctx = await _entorno(session)
        _carga_id, numero = await _carga(session, ctx, "IN_TRANSIT")

        resultado = await _proponer(session, redis, ctx, numero[:-2], "RECEIVED")

        assert resultado["encontrada"] is False

    async def test_no_propone_retroceder(self, session: AsyncSession, redis) -> None:
        """Retroceder pide justificación: no es un avance que AMVI pueda hacer."""
        ctx = await _entorno(session)
        _carga_id, numero = await _carga(session, ctx, "STORED")
        antes = await _cuenta_propuestas(session)

        resultado = await _proponer(session, redis, ctx, numero, "RECEIVED")

        assert resultado["propuesta"] is None
        assert "motivo" in resultado
        assert await _cuenta_propuestas(session) == antes

    async def test_no_propone_fuera_del_ingreso_a_bodega(
        self, session: AsyncSession, redis
    ) -> None:
        """Desde Almacenada lo que sigue es el flujo de despachos, no un cambio suelto."""
        ctx = await _entorno(session)
        _carga_id, numero = await _carga(session, ctx, "DISPATCH_REQUESTED")

        resultado = await _proponer(session, redis, ctx, numero, "STORED")

        assert resultado["propuesta"] is None

    async def test_un_primer_paso_bloqueado_explica_y_no_propone(
        self, session: AsyncSession, redis, monkeypatch
    ) -> None:
        ctx = await _entorno(session)
        _carga_id, numero = await _carga(session, ctx, "RECEIVED")

        async def bloqueada(*_args, **_kwargs):
            return [
                cargas.TransicionDisponible(
                    to_status="STORED",
                    label="Almacenada",
                    requires_reason=False,
                    blocked=True,
                    blockers=[{"code": "WR_REQUERIDO", "message": "Falta el WR.", "details": []}],
                )
            ]

        monkeypatch.setattr(cargas, "transiciones_disponibles", bloqueada)

        resultado = await _proponer(session, redis, ctx, numero, "STORED")

        assert resultado["propuesta"] is None
        assert "Falta el WR." in resultado["motivo"]

    async def test_un_cliente_no_tiene_la_herramienta(self, session: AsyncSession, redis) -> None:
        ctx = await _entorno(session)
        permisos = await _permisos(session, redis, ctx["cliente_a"])

        assert puede_ejecutar("proponer_cambio_estado", permisos) is False


async def _cuenta_propuestas(session: AsyncSession) -> int:
    return int(
        (await session.execute(text("SELECT count(*) FROM copilot_action_proposals"))).scalar_one()
    )


class TestConfirmacion:
    async def test_aplica_los_pasos_con_el_motor_de_transiciones(
        self, session: AsyncSession, redis
    ) -> None:
        ctx = await _entorno(session)
        carga, numero = await _carga(session, ctx, "PRE_ALERT")
        resultado = await _proponer(session, redis, ctx, numero, "STORED")
        permisos = await _permisos(session, redis, ctx["operaciones"])

        confirmado = await confirmaciones.confirmar_proponer_cambio_estado(
            session, permisos, uuid.UUID(resultado["id"]), {}
        )

        assert confirmado["estado"] == "STORED"
        assert await _estado(session, carga) == "STORED"
        eventos = (
            await session.execute(
                text("""
                    SELECT count(*) FROM shipment_events
                    WHERE shipment_id = :s AND event_type = 'STATUS_CHANGED'
                """),
                {"s": carga},
            )
        ).scalar_one()
        assert eventos == 3
        fechas = (
            await session.execute(
                text("SELECT received_at, stored_at FROM shipments WHERE id = :s"), {"s": carga}
            )
        ).one()
        assert fechas.received_at is not None and fechas.stored_at is not None

    async def test_si_la_carga_cambio_desde_la_propuesta_no_aplica_nada(
        self, session: AsyncSession, redis
    ) -> None:
        """La propuesta se hizo sobre una versión; si otra persona la movió, se revisa de nuevo."""
        ctx = await _entorno(session)
        carga, numero = await _carga(session, ctx, "IN_TRANSIT")
        resultado = await _proponer(session, redis, ctx, numero, "RECEIVED")
        await session.execute(
            text("UPDATE shipments SET row_version = row_version + 1 WHERE id = :s"), {"s": carga}
        )
        permisos = await _permisos(session, redis, ctx["operaciones"])

        with pytest.raises(ErrorDeAplicacion):
            await confirmaciones.confirmar_proponer_cambio_estado(
                session, permisos, uuid.UUID(resultado["id"]), {}
            )

        assert await _estado(session, carga) == "IN_TRANSIT"

    async def test_revalida_el_permiso_al_confirmar(self, session: AsyncSession, redis) -> None:
        """Si quien confirma ya no puede avanzar estados, falla como error de la
        aplicación (y la propuesta queda FAILED), no como un 500."""
        ctx = await _entorno(session)
        _carga_id, numero = await _carga(session, ctx, "IN_TRANSIT")
        resultado = await _proponer(session, redis, ctx, numero, "RECEIVED")
        permisos_cliente = await _permisos(session, redis, ctx["cliente_a"])

        with pytest.raises(SinPermiso):
            await confirmaciones.confirmar_proponer_cambio_estado(
                session, permisos_cliente, uuid.UUID(resultado["id"]), {}
            )

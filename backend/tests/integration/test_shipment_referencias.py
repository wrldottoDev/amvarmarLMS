"""Referencias, paquetes y timeline (Paso 2.3)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from scripts.seed_rbac import sembrar as sembrar_rbac
from scripts.seed_shipment_statuses import sembrar as sembrar_estados
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.shipments.models import EventType, ReferenceType
from app.modules.shipments.policies import (
    WarehouseReceiptFaltante,
    WarehouseReceiptNoAplica,
    bodega_usa_warehouse_receipt,
    validar_referencia_permitida,
    validar_wr_presente_para_almacenar,
)

pytestmark = pytest.mark.integration


async def _contexto(session: AsyncSession) -> dict[str, uuid.UUID]:
    """Dos orígenes: una bodega que emite WR (Miami) y otra que no (Shanghái)."""
    await sembrar_rbac(session)
    await sembrar_estados(session)

    company_id = (
        await session.execute(
            text(
                "INSERT INTO companies (legal_name, status) "
                "VALUES ('Refs S.A.', 'ACTIVE') RETURNING id"
            )
        )
    ).scalar_one()
    user_id = (
        await session.execute(
            text("""
                INSERT INTO users (email, password_hash, first_name, last_name, status)
                VALUES (:e, 'h', 'N', 'A', 'ACTIVE') RETURNING id
            """),
            {"e": f"refs-{uuid.uuid4().hex[:8]}@amvarmar.com"},
        )
    ).scalar_one()

    miami = await _ubicacion(session, "US", "MIA", "Miami")
    shanghai = await _ubicacion(session, "CN", "SHA", "Shanghái")
    destino = await _ubicacion(session, "CR", "SJO", "San José")

    bodega_miami = (
        await session.execute(
            text("""
                INSERT INTO facilities
                    (location_id, facility_code, facility_type, uses_warehouse_receipt)
                VALUES (:loc, 'MIA-WH-01', 'WAREHOUSE', true)
                ON CONFLICT (facility_code) DO UPDATE SET uses_warehouse_receipt = true
                RETURNING id
            """),
            {"loc": miami},
        )
    ).scalar_one()

    # Oficina en Shanghái: existe como instalación, pero no emite WR.
    oficina_sha = (
        await session.execute(
            text("""
                INSERT INTO facilities
                    (location_id, facility_code, facility_type, uses_warehouse_receipt)
                VALUES (:loc, 'SHA-OF-01', 'OFFICE', false)
                ON CONFLICT (facility_code) DO UPDATE SET uses_warehouse_receipt = false
                RETURNING id
            """),
            {"loc": shanghai},
        )
    ).scalar_one()

    return {
        "company_id": company_id,
        "user_id": user_id,
        "miami": miami,
        "shanghai": shanghai,
        "destino": destino,
        "bodega_miami": bodega_miami,
        "oficina_sha": oficina_sha,
    }


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


async def _crear_carga(
    session: AsyncSession,
    ctx: dict[str, uuid.UUID],
    *,
    origen: str = "miami",
    con_bodega: bool = True,
) -> uuid.UUID:
    facility = None
    if con_bodega:
        facility = ctx["bodega_miami"] if origen == "miami" else ctx["oficina_sha"]

    return (
        await session.execute(
            text("""
                INSERT INTO shipments
                    (company_id, created_by, current_status_code,
                     origin_location_id, origin_facility_id, destination_location_id)
                VALUES (:c, :u, 'PRE_ALERT', :loc, :fac, :dest)
                RETURNING id
            """),
            {
                "c": ctx["company_id"],
                "u": ctx["user_id"],
                "loc": ctx[origen],
                "fac": facility,
                "dest": ctx["destino"],
            },
        )
    ).scalar_one()


async def _agregar_referencia(
    session: AsyncSession, shipment_id: uuid.UUID, tipo: str, valor: str
) -> uuid.UUID:
    return (
        await session.execute(
            text("""
                INSERT INTO shipment_references (shipment_id, reference_type, value)
                VALUES (:s, :t, :v) RETURNING id
            """),
            {"s": shipment_id, "t": tipo, "v": valor},
        )
    ).scalar_one()


class TestReglaDeMiami:
    async def test_una_carga_de_miami_admite_wr(self, session: AsyncSession) -> None:
        ctx = await _contexto(session)
        shipment_id = await _crear_carga(session, ctx, origen="miami")

        await validar_referencia_permitida(
            session, shipment_id=shipment_id, reference_type=ReferenceType.WR
        )

        assert await bodega_usa_warehouse_receipt(session, shipment_id) is True

    async def test_una_carga_de_china_no_admite_wr(self, session: AsyncSession) -> None:
        """El otro sentido del gate: sin bodega que lo emita, no hay WR."""
        ctx = await _contexto(session)
        shipment_id = await _crear_carga(session, ctx, origen="shanghai")

        with pytest.raises(WarehouseReceiptNoAplica) as error:
            await validar_referencia_permitida(
                session, shipment_id=shipment_id, reference_type=ReferenceType.WR
            )

        assert error.value.code == "WR_NO_APLICA_A_ESTE_ORIGEN"
        assert error.value.status_code == 422

    async def test_una_carga_sin_bodega_de_origen_no_admite_wr(self, session: AsyncSession) -> None:
        """`origin_facility_id` nulo: AMVARMAR no tiene instalación ahí."""
        ctx = await _contexto(session)
        shipment_id = await _crear_carga(session, ctx, origen="shanghai", con_bodega=False)

        with pytest.raises(WarehouseReceiptNoAplica):
            await validar_referencia_permitida(
                session, shipment_id=shipment_id, reference_type=ReferenceType.WR
            )

    @pytest.mark.parametrize(
        "tipo",
        [
            ReferenceType.INVOICE,
            ReferenceType.PO,
            ReferenceType.TRACKING,
            ReferenceType.CONTAINER,
            ReferenceType.BL,
            ReferenceType.OTHER,
        ],
    )
    async def test_las_demas_referencias_no_dependen_del_origen(
        self, session: AsyncSession, tipo: str
    ) -> None:
        ctx = await _contexto(session)
        shipment_id = await _crear_carga(session, ctx, origen="shanghai")

        await validar_referencia_permitida(session, shipment_id=shipment_id, reference_type=tipo)

    async def test_miami_exige_wr_antes_de_almacenar(self, session: AsyncSession) -> None:
        """Dirección inversa (ADR-0005): la regla no solo prohíbe, también exige."""
        ctx = await _contexto(session)
        shipment_id = await _crear_carga(session, ctx, origen="miami")

        with pytest.raises(WarehouseReceiptFaltante) as error:
            await validar_wr_presente_para_almacenar(session, shipment_id)

        assert error.value.code == "SHIPMENT_MISSING_WR"

    async def test_con_wr_registrado_miami_puede_almacenar(self, session: AsyncSession) -> None:
        ctx = await _contexto(session)
        shipment_id = await _crear_carga(session, ctx, origen="miami")
        await _agregar_referencia(session, shipment_id, ReferenceType.WR, "WR105921")

        await validar_wr_presente_para_almacenar(session, shipment_id)

    async def test_una_carga_de_china_almacena_sin_wr(self, session: AsyncSession) -> None:
        ctx = await _contexto(session)
        shipment_id = await _crear_carga(session, ctx, origen="shanghai")

        await validar_wr_presente_para_almacenar(session, shipment_id)


class TestReferencias:
    async def test_se_busca_una_carga_por_su_factura(self, session: AsyncSession) -> None:
        """Es como el cliente la encuentra sin recordar el shipment_number."""
        ctx = await _contexto(session)
        shipment_id = await _crear_carga(session, ctx)
        await _agregar_referencia(session, shipment_id, ReferenceType.INVOICE, "FAC-99123")

        encontrado = (
            await session.execute(
                text("""
                    SELECT shipment_id FROM shipment_references
                    WHERE reference_type = 'INVOICE' AND value = :v
                """),
                {"v": "FAC-99123"},
            )
        ).scalar_one()

        assert encontrado == shipment_id

    async def test_no_se_repite_la_misma_referencia_en_una_carga(
        self, session: AsyncSession
    ) -> None:
        ctx = await _contexto(session)
        shipment_id = await _crear_carga(session, ctx)
        await _agregar_referencia(session, shipment_id, ReferenceType.INVOICE, "FAC-1")

        with pytest.raises(IntegrityError):
            await _agregar_referencia(session, shipment_id, ReferenceType.INVOICE, "FAC-1")

    async def test_dos_cargas_pueden_compartir_numero_de_factura(
        self, session: AsyncSession
    ) -> None:
        """Una factura puede amparar varias cargas: la unicidad es por carga."""
        ctx = await _contexto(session)
        una = await _crear_carga(session, ctx)
        otra = await _crear_carga(session, ctx)

        await _agregar_referencia(session, una, ReferenceType.INVOICE, "FAC-COMPARTIDA")
        await _agregar_referencia(session, otra, ReferenceType.INVOICE, "FAC-COMPARTIDA")

    async def test_valor_vacio_rechazado(self, session: AsyncSession) -> None:
        ctx = await _contexto(session)
        shipment_id = await _crear_carga(session, ctx)

        with pytest.raises(IntegrityError):
            await _agregar_referencia(session, shipment_id, ReferenceType.INVOICE, "   ")

    async def test_tipo_de_referencia_invalido_rechazado(self, session: AsyncSession) -> None:
        ctx = await _contexto(session)
        shipment_id = await _crear_carga(session, ctx)

        with pytest.raises(IntegrityError):
            await _agregar_referencia(session, shipment_id, "INVENTADO", "X")


class TestPaquetes:
    async def test_se_registran_los_bultos(self, session: AsyncSession) -> None:
        ctx = await _contexto(session)
        shipment_id = await _crear_carga(session, ctx)

        await session.execute(
            text("""
                INSERT INTO shipment_packages
                    (shipment_id, package_type, quantity, description, weight_kg)
                VALUES (:s, 'PALLET', 4, 'Repuestos', 820.5)
            """),
            {"s": shipment_id},
        )

        fila = (
            await session.execute(
                text("""
                    SELECT package_type, quantity, weight_kg FROM shipment_packages
                    WHERE shipment_id = :s
                """),
                {"s": shipment_id},
            )
        ).one()
        assert fila.package_type == "PALLET"
        assert fila.quantity == 4

    async def test_cantidad_cero_rechazada(self, session: AsyncSession) -> None:
        ctx = await _contexto(session)
        shipment_id = await _crear_carga(session, ctx)

        with pytest.raises(IntegrityError):
            await session.execute(
                text("""
                    INSERT INTO shipment_packages (shipment_id, package_type, quantity)
                    VALUES (:s, 'BOX', 0)
                """),
                {"s": shipment_id},
            )

    async def test_tipo_de_bulto_invalido_rechazado(self, session: AsyncSession) -> None:
        ctx = await _contexto(session)
        shipment_id = await _crear_carga(session, ctx)

        with pytest.raises(IntegrityError):
            await session.execute(
                text("""
                    INSERT INTO shipment_packages (shipment_id, package_type, quantity)
                    VALUES (:s, 'CONTENEDOR', 1)
                """),
                {"s": shipment_id},
            )


async def _agregar_evento(
    session: AsyncSession,
    shipment_id: uuid.UUID,
    *,
    tipo: str = EventType.NOTE,
    titulo: str = "Evento",
    occurred_at: datetime | None = None,
) -> uuid.UUID:
    return (
        await session.execute(
            text("""
                INSERT INTO shipment_events (shipment_id, event_type, title, occurred_at)
                VALUES (:s, :t, :ti, :o) RETURNING id
            """),
            {
                "s": shipment_id,
                "t": tipo,
                "ti": titulo,
                "o": occurred_at or datetime.now(UTC),
            },
        )
    ).scalar_one()


class TestTimelineInmutable:
    async def test_no_se_puede_editar_un_evento(self, session: AsyncSession) -> None:
        """El trigger lo impide aunque el UPDATE venga de SQL directo."""
        ctx = await _contexto(session)
        shipment_id = await _crear_carga(session, ctx)
        evento_id = await _agregar_evento(session, shipment_id, titulo="Original")

        with pytest.raises(DBAPIError) as error:
            await session.execute(
                text("UPDATE shipment_events SET title = 'Alterado' WHERE id = :id"),
                {"id": evento_id},
            )

        assert "append-only" in str(error.value)

    async def test_no_se_puede_borrar_un_evento(self, session: AsyncSession) -> None:
        ctx = await _contexto(session)
        shipment_id = await _crear_carga(session, ctx)
        evento_id = await _agregar_evento(session, shipment_id)

        with pytest.raises(DBAPIError) as error:
            await session.execute(
                text("DELETE FROM shipment_events WHERE id = :id"), {"id": evento_id}
            )

        assert "append-only" in str(error.value)

    async def test_una_correccion_se_agrega_no_reemplaza(self, session: AsyncSession) -> None:
        """Corregir deja los dos registros visibles: el error y su explicación."""
        ctx = await _contexto(session)
        shipment_id = await _crear_carga(session, ctx)
        await _agregar_evento(session, shipment_id, titulo="Peso 500 kg")

        await _agregar_evento(
            session,
            shipment_id,
            tipo=EventType.CORRECTION,
            titulo="Corrección: el peso real es 820 kg",
        )

        total = (
            await session.execute(
                text("SELECT count(*) FROM shipment_events WHERE shipment_id = :s"),
                {"s": shipment_id},
            )
        ).scalar_one()
        assert total == 2

    async def test_no_se_puede_borrar_una_carga_con_historia(self, session: AsyncSession) -> None:
        """RESTRICT: borrar la carga no puede borrar su historia."""
        ctx = await _contexto(session)
        shipment_id = await _crear_carga(session, ctx)
        await _agregar_evento(session, shipment_id)

        with pytest.raises(IntegrityError):
            await session.execute(text("DELETE FROM shipments WHERE id = :id"), {"id": shipment_id})


class TestTimelineOrden:
    async def test_el_orden_es_estable_con_timestamps_iguales(self, session: AsyncSession) -> None:
        """Sin desempate por `id`, dos eventos simultáneos saldrían en orden
        arbitrario y distinto en cada consulta."""
        ctx = await _contexto(session)
        shipment_id = await _crear_carga(session, ctx)
        momento = datetime.now(UTC)

        for i in range(5):
            await _agregar_evento(session, shipment_id, titulo=f"Evento {i}", occurred_at=momento)

        consultas = []
        for _ in range(3):
            ids = (
                (
                    await session.execute(
                        text("""
                            SELECT id FROM shipment_events
                            WHERE shipment_id = :s
                            ORDER BY occurred_at DESC, id
                        """),
                        {"s": shipment_id},
                    )
                )
                .scalars()
                .all()
            )
            consultas.append(list(ids))

        assert consultas[0] == consultas[1] == consultas[2]

    async def test_un_movimiento_atrasado_no_falsea_la_auditoria(
        self, session: AsyncSession
    ) -> None:
        """`occurred_at` es cuándo pasó; `recorded_at`, cuándo se cargó."""
        ctx = await _contexto(session)
        shipment_id = await _crear_carga(session, ctx)
        hace_tres_dias = datetime.now(UTC) - timedelta(days=3)

        evento_id = await _agregar_evento(
            session, shipment_id, titulo="Recibido el lunes", occurred_at=hace_tres_dias
        )

        fila = (
            await session.execute(
                text("SELECT occurred_at, recorded_at FROM shipment_events WHERE id = :id"),
                {"id": evento_id},
            )
        ).one()
        assert fila.occurred_at < fila.recorded_at

    async def test_el_timeline_sale_del_mas_reciente_al_mas_viejo(
        self, session: AsyncSession
    ) -> None:
        ctx = await _contexto(session)
        shipment_id = await _crear_carga(session, ctx)
        base = datetime.now(UTC)

        for dias, titulo in [(3, "Primero"), (2, "Segundo"), (1, "Tercero")]:
            await _agregar_evento(
                session,
                shipment_id,
                titulo=titulo,
                occurred_at=base - timedelta(days=dias),
            )

        titulos = (
            (
                await session.execute(
                    text("""
                        SELECT title FROM shipment_events
                        WHERE shipment_id = :s ORDER BY occurred_at DESC, id
                    """),
                    {"s": shipment_id},
                )
            )
            .scalars()
            .all()
        )

        assert list(titulos) == ["Tercero", "Segundo", "Primero"]


class TestEventoDeCambioDeEstado:
    """El motor de transiciones llega en el Paso 2.4; aquí se verifica que la
    tabla soporta correctamente lo que ese motor va a escribir."""

    async def test_un_cambio_de_estado_deja_un_solo_evento(self, session: AsyncSession) -> None:
        ctx = await _contexto(session)
        shipment_id = await _crear_carga(session, ctx)

        # Lo que hará `POST /shipments/{id}/transitions`, en una transacción.
        await session.execute(
            text("""
                UPDATE shipments
                SET current_status_code = 'IN_TRANSIT', row_version = row_version + 1
                WHERE id = :s AND row_version = 1
            """),
            {"s": shipment_id},
        )
        await session.execute(
            text("""
                INSERT INTO shipment_events
                    (shipment_id, event_type, from_status_code, to_status_code,
                     title, occurred_at, actor_user_id)
                VALUES (:s, 'STATUS_CHANGED', 'PRE_ALERT', 'IN_TRANSIT',
                        'La carga salió del origen', now(), :u)
            """),
            {"s": shipment_id, "u": ctx["user_id"]},
        )

        eventos = (
            await session.execute(
                text("""
                    SELECT from_status_code, to_status_code, event_type
                    FROM shipment_events WHERE shipment_id = :s
                """),
                {"s": shipment_id},
            )
        ).all()

        assert len(eventos) == 1
        assert eventos[0].from_status_code == "PRE_ALERT"
        assert eventos[0].to_status_code == "IN_TRANSIT"

    async def test_un_evento_no_puede_apuntar_a_un_estado_inexistente(
        self, session: AsyncSession
    ) -> None:
        ctx = await _contexto(session)
        shipment_id = await _crear_carga(session, ctx)

        with pytest.raises(IntegrityError):
            await session.execute(
                text("""
                    INSERT INTO shipment_events
                        (shipment_id, event_type, to_status_code, title, occurred_at)
                    VALUES (:s, 'STATUS_CHANGED', 'INVENTADO', 'X', now())
                """),
                {"s": shipment_id},
            )

    async def test_los_metadatos_arrancan_vacios(self, session: AsyncSession) -> None:
        ctx = await _contexto(session)
        shipment_id = await _crear_carga(session, ctx)
        evento_id = await _agregar_evento(session, shipment_id)

        datos = (
            await session.execute(
                text("SELECT metadata FROM shipment_events WHERE id = :id"), {"id": evento_id}
            )
        ).scalar_one()

        assert datos == {}

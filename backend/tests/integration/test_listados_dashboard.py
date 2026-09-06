"""Listados con cursor, timeline y dashboard (Paso 2.5)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from scripts.seed_document_types import sembrar as sembrar_documentos
from scripts.seed_rbac import sembrar as sembrar_rbac
from scripts.seed_shipment_statuses import sembrar as sembrar_estados
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import LIMITE_MAXIMO, Cursor, CursorInvalido, normalizar_limite
from app.modules.rbac.models import RoleCode, ScopeType
from app.modules.rbac.service import obtener_permisos_efectivos
from app.modules.shipments import queries, service
from app.modules.shipments.models import (
    ReferenceType,
    RequirementType,
    ShipmentStatus,
)

pytestmark = pytest.mark.integration


async def _entorno(session: AsyncSession) -> dict[str, object]:
    await sembrar_rbac(session)
    await sembrar_estados(session)
    await sembrar_documentos(session)

    empresa_a = await _empresa(session, "Empresa A S.A.")
    empresa_b = await _empresa(session, "Empresa B S.A.")

    cliente_a = await _usuario_con_rol(
        session, RoleCode.CLIENT_ADMIN, ScopeType.ORGANIZATION, empresa_a
    )
    cliente_b = await _usuario_con_rol(
        session, RoleCode.CLIENT_ADMIN, ScopeType.ORGANIZATION, empresa_b
    )
    operaciones = await _usuario_con_rol(session, RoleCode.OPS_ADMIN, ScopeType.GLOBAL, None)
    sin_rol = await _usuario(session)

    origen = await _ubicacion(session, "US", "MIA", "Miami")
    destino = await _ubicacion(session, "CR", "SJO", "San José")

    return {
        "empresa_a": empresa_a,
        "empresa_b": empresa_b,
        "cliente_a": cliente_a,
        "cliente_b": cliente_b,
        "operaciones": operaciones,
        "sin_rol": sin_rol,
        "origen": origen,
        "destino": destino,
    }


async def _empresa(session: AsyncSession, nombre: str) -> uuid.UUID:
    return (
        await session.execute(
            text("INSERT INTO companies (legal_name, status) VALUES (:n, 'ACTIVE') RETURNING id"),
            {"n": nombre},
        )
    ).scalar_one()


async def _usuario(session: AsyncSession) -> uuid.UUID:
    return (
        await session.execute(
            text("""
                INSERT INTO users (email, password_hash, first_name, last_name, status)
                VALUES (:e, 'h', 'Ana', 'Prueba', 'ACTIVE') RETURNING id
            """),
            {"e": f"u-{uuid.uuid4().hex[:10]}@amvarmar.com"},
        )
    ).scalar_one()


async def _usuario_con_rol(
    session: AsyncSession, rol: str, scope: str, empresa: uuid.UUID | None
) -> uuid.UUID:
    user_id = await _usuario(session)
    await session.execute(
        text("""
            INSERT INTO user_role_assignments (user_id, role_id, scope_type, company_id)
            SELECT :u, r.id, :scope, :c FROM roles r WHERE r.code = :rol
        """),
        {"u": user_id, "rol": rol, "scope": scope, "c": empresa},
    )
    if empresa is not None:
        await session.execute(
            text("""
                INSERT INTO company_memberships (company_id, user_id, status)
                VALUES (:c, :u, 'ACTIVE')
            """),
            {"c": empresa, "u": user_id},
        )
    return user_id


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


async def _carga(
    session: AsyncSession,
    ctx: dict[str, object],
    *,
    empresa: str = "empresa_a",
    estado: str = ShipmentStatus.PRE_ALERT,
    eta: datetime | None = None,
    entregada_el: datetime | None = None,
) -> uuid.UUID:
    return (
        await session.execute(
            text("""
                INSERT INTO shipments
                    (company_id, created_by, current_status_code, origin_location_id,
                     destination_location_id, estimated_arrival_at, delivered_at)
                VALUES (:c, :u, :estado, :o, :d, :eta, :entregada)
                RETURNING id
            """),
            {
                "c": ctx[empresa],
                "u": ctx["operaciones"],
                "estado": estado,
                "o": ctx["origen"],
                "d": ctx["destino"],
                "eta": eta,
                "entregada": entregada_el,
            },
        )
    ).scalar_one()


async def _permisos(session: AsyncSession, redis, user_id: uuid.UUID):
    return await obtener_permisos_efectivos(session, redis, user_id)


class TestAislamientoEnListados:
    async def test_un_cliente_solo_ve_las_cargas_de_su_empresa(
        self, session: AsyncSession, redis
    ) -> None:
        ctx = await _entorno(session)
        propia = await _carga(session, ctx, empresa="empresa_a")
        await _carga(session, ctx, empresa="empresa_b")

        pagina = await queries.listar_shipments(
            session,
            permisos=await _permisos(session, redis, ctx["cliente_a"]),
            filtros=queries.FiltrosListado(),
            limite=50,
        )

        assert [f.id for f in pagina.items] == [propia]

    async def test_operaciones_ve_las_de_todas_las_empresas(
        self, session: AsyncSession, redis
    ) -> None:
        ctx = await _entorno(session)
        await _carga(session, ctx, empresa="empresa_a")
        await _carga(session, ctx, empresa="empresa_b")

        pagina = await queries.listar_shipments(
            session,
            permisos=await _permisos(session, redis, ctx["operaciones"]),
            filtros=queries.FiltrosListado(),
            limite=50,
        )

        assert len(pagina.items) == 2

    async def test_pedir_otra_empresa_no_devuelve_nada(self, session: AsyncSession, redis) -> None:
        """El filtro del usuario se SUMA al de alcance, no lo reemplaza."""
        ctx = await _entorno(session)
        await _carga(session, ctx, empresa="empresa_b")

        pagina = await queries.listar_shipments(
            session,
            permisos=await _permisos(session, redis, ctx["cliente_a"]),
            filtros=queries.FiltrosListado(company_id=ctx["empresa_b"]),
            limite=50,
        )

        assert pagina.items == []

    async def test_sin_rol_no_ve_nada(self, session: AsyncSession, redis) -> None:
        ctx = await _entorno(session)
        await _carga(session, ctx)

        pagina = await queries.listar_shipments(
            session,
            permisos=await _permisos(session, redis, ctx["sin_rol"]),
            filtros=queries.FiltrosListado(),
            limite=50,
        )

        assert pagina.items == []

    async def test_el_detalle_de_otra_empresa_da_404(self, session: AsyncSession, redis) -> None:
        from app.core.errors import RecursoNoEncontrado

        ctx = await _entorno(session)
        ajena = await _carga(session, ctx, empresa="empresa_b")

        with pytest.raises(RecursoNoEncontrado):
            await queries.obtener_shipment(
                session,
                shipment_id=ajena,
                permisos=await _permisos(session, redis, ctx["cliente_a"]),
            )

    async def test_el_timeline_de_otra_empresa_da_404(self, session: AsyncSession, redis) -> None:
        """Sin este chequeo, el timeline sería una vía lateral a datos ajenos."""
        from app.core.errors import RecursoNoEncontrado

        ctx = await _entorno(session)
        ajena = await _carga(session, ctx, empresa="empresa_b")

        with pytest.raises(RecursoNoEncontrado):
            await queries.timeline(
                session,
                shipment_id=ajena,
                permisos=await _permisos(session, redis, ctx["cliente_a"]),
                limite=10,
            )


class TestCursor:
    def test_el_cursor_es_opaco_y_reversible(self) -> None:
        original = Cursor(created_at=datetime.now(UTC), id=uuid.uuid4())

        recuperado = Cursor.decodificar(original.codificar())

        assert recuperado.id == original.id
        # Sin padding: viaja limpio en una query string.
        assert "=" not in original.codificar()

    @pytest.mark.parametrize("basura", ["", "no-es-base64!!", "eyJ4IjoxfQ", "MTIz"])
    def test_un_cursor_invalido_no_revienta(self, basura: str) -> None:
        with pytest.raises(CursorInvalido):
            Cursor.decodificar(basura)

    def test_el_limite_se_recorta_al_maximo(self) -> None:
        """Pedir 10000 devuelve el máximo, no un error."""
        assert normalizar_limite(10000) == LIMITE_MAXIMO
        assert normalizar_limite(0) == 1
        assert normalizar_limite(-5) == 1
        assert normalizar_limite(10) == 10

    async def test_recorrer_todas_las_paginas_no_repite_ni_omite(
        self, session: AsyncSession, redis
    ) -> None:
        ctx = await _entorno(session)
        esperados = {await _carga(session, ctx) for _ in range(23)}
        permisos = await _permisos(session, redis, ctx["operaciones"])

        vistos: list[uuid.UUID] = []
        cursor = None
        for _ in range(20):  # tope de seguridad contra un bucle infinito
            pagina = await queries.listar_shipments(
                session,
                permisos=permisos,
                filtros=queries.FiltrosListado(),
                limite=5,
                cursor=cursor,
            )
            vistos.extend(f.id for f in pagina.items)
            if not pagina.has_more:
                break
            cursor = Cursor.decodificar(pagina.next_cursor)

        assert len(vistos) == len(set(vistos)), "hubo filas repetidas"
        assert set(vistos) == esperados

    async def test_insertar_mientras_se_pagina_no_desordena(
        self, session: AsyncSession, redis
    ) -> None:
        """Lo que hace fallar a OFFSET: una inserción desplaza las páginas.

        Con cursor, lo nuevo aparece arriba y no altera lo ya servido.
        """
        ctx = await _entorno(session)
        originales = {await _carga(session, ctx) for _ in range(10)}
        permisos = await _permisos(session, redis, ctx["operaciones"])

        primera = await queries.listar_shipments(
            session, permisos=permisos, filtros=queries.FiltrosListado(), limite=4
        )
        vistos = [f.id for f in primera.items]

        # Llega una carga nueva a mitad del recorrido.
        await _carga(session, ctx)

        cursor = Cursor.decodificar(primera.next_cursor)
        while cursor is not None:
            pagina = await queries.listar_shipments(
                session,
                permisos=permisos,
                filtros=queries.FiltrosListado(),
                limite=4,
                cursor=cursor,
            )
            vistos.extend(f.id for f in pagina.items)
            cursor = Cursor.decodificar(pagina.next_cursor) if pagina.has_more else None

        assert len(vistos) == len(set(vistos)), "la inserción causó repeticiones"
        assert originales.issubset(set(vistos)), "se omitieron filas originales"


class TestFiltros:
    async def test_filtra_por_estado(self, session: AsyncSession, redis) -> None:
        ctx = await _entorno(session)
        en_transito = await _carga(session, ctx, estado=ShipmentStatus.IN_TRANSIT)
        await _carga(session, ctx, estado=ShipmentStatus.STORED)

        pagina = await queries.listar_shipments(
            session,
            permisos=await _permisos(session, redis, ctx["operaciones"]),
            filtros=queries.FiltrosListado(estados=[ShipmentStatus.IN_TRANSIT]),
            limite=50,
        )

        assert [f.id for f in pagina.items] == [en_transito]

    async def test_busca_por_numero_de_factura(self, session: AsyncSession, redis) -> None:
        """Es como el cliente encuentra su carga sin recordar el shipment_number."""
        ctx = await _entorno(session)
        con_factura = await _carga(session, ctx)
        await _carga(session, ctx)
        await session.execute(
            text("""
                INSERT INTO shipment_references (shipment_id, reference_type, value)
                VALUES (:s, :t, 'FAC-99123')
            """),
            {"s": con_factura, "t": ReferenceType.INVOICE},
        )

        pagina = await queries.listar_shipments(
            session,
            permisos=await _permisos(session, redis, ctx["operaciones"]),
            filtros=queries.FiltrosListado(texto="99123"),
            limite=50,
        )

        assert [f.id for f in pagina.items] == [con_factura]

    async def test_filtra_por_rango_de_eta(self, session: AsyncSession, redis) -> None:
        ctx = await _entorno(session)
        pronto = await _carga(session, ctx, eta=datetime.now(UTC) + timedelta(days=2))
        await _carga(session, ctx, eta=datetime.now(UTC) + timedelta(days=60))

        pagina = await queries.listar_shipments(
            session,
            permisos=await _permisos(session, redis, ctx["operaciones"]),
            filtros=queries.FiltrosListado(eta_hasta=datetime.now(UTC) + timedelta(days=7)),
            limite=50,
        )

        assert [f.id for f in pagina.items] == [pronto]

    async def test_las_archivadas_no_salen_por_defecto(self, session: AsyncSession, redis) -> None:
        """ADR-0007: lo archivado sale del flujo operativo."""
        ctx = await _entorno(session)
        activa = await _carga(session, ctx)
        archivada = await _carga(session, ctx)
        await session.execute(
            text("UPDATE shipments SET archived_at = now() WHERE id = :s"), {"s": archivada}
        )
        permisos = await _permisos(session, redis, ctx["operaciones"])

        normal = await queries.listar_shipments(
            session, permisos=permisos, filtros=queries.FiltrosListado(), limite=50
        )
        historial = await queries.listar_shipments(
            session,
            permisos=permisos,
            filtros=queries.FiltrosListado(incluir_archivadas=True),
            limite=50,
        )

        assert [f.id for f in normal.items] == [activa]
        assert len(historial.items) == 2

    async def test_solo_archivadas_no_mezcla_con_activas(
        self, session: AsyncSession, redis
    ) -> None:
        """Historial de despachos: SOLO archivadas, no todas + archivadas."""
        ctx = await _entorno(session)
        await _carga(session, ctx)
        archivada = await _carga(session, ctx)
        await session.execute(
            text("UPDATE shipments SET archived_at = now() WHERE id = :s"), {"s": archivada}
        )
        permisos = await _permisos(session, redis, ctx["operaciones"])

        historial = await queries.listar_shipments(
            session,
            permisos=permisos,
            filtros=queries.FiltrosListado(solo_archivadas=True),
            limite=50,
        )

        assert [f.id for f in historial.items] == [archivada]

    async def test_las_borradas_nunca_salen(self, session: AsyncSession, redis) -> None:
        ctx = await _entorno(session)
        await _carga(session, ctx)
        borrada = await _carga(session, ctx)
        await session.execute(
            text("UPDATE shipments SET deleted_at = now() WHERE id = :s"), {"s": borrada}
        )

        pagina = await queries.listar_shipments(
            session,
            permisos=await _permisos(session, redis, ctx["operaciones"]),
            filtros=queries.FiltrosListado(incluir_archivadas=True),
            limite=50,
        )

        assert borrada not in [f.id for f in pagina.items]


class TestEstadoYPendientesSeparados:
    async def test_el_estado_no_lo_reemplaza_lo_pendiente(
        self, session: AsyncSession, redis
    ) -> None:
        """La regla de interfaz del proyecto, verificada en la API.

        Una carga `IN_TRANSIT` con requisitos abiertos sigue reportando
        `IN_TRANSIT` como estado, y lo pendiente viaja en un campo aparte.
        """
        ctx = await _entorno(session)
        shipment_id = await _carga(session, ctx, estado=ShipmentStatus.IN_TRANSIT)
        tipo_id = (
            await session.execute(
                text("SELECT id FROM document_types WHERE code = 'COMMERCIAL_INVOICE'")
            )
        ).scalar_one()
        await service.abrir_requisito(
            session,
            shipment_id=shipment_id,
            requirement_type=RequirementType.DOCUMENT,
            title="Factura comercial",
            required_from="CLIENT",
            actor_user_id=ctx["operaciones"],
            permisos=await _permisos(session, redis, ctx["operaciones"]),
            document_type_id=tipo_id,
        )

        pagina = await queries.listar_shipments(
            session,
            permisos=await _permisos(session, redis, ctx["operaciones"]),
            filtros=queries.FiltrosListado(),
            limite=50,
        )

        fila = pagina.items[0]
        assert fila.current_status_code == ShipmentStatus.IN_TRANSIT
        assert fila.requisitos_abiertos == 1
        assert fila.requisitos_del_cliente == 1

    async def test_lo_pendiente_de_operaciones_no_cuenta_para_el_cliente(
        self, session: AsyncSession, redis
    ) -> None:
        """Un packing list pendiente es de Operaciones (ADR-0003)."""
        ctx = await _entorno(session)
        shipment_id = await _carga(session, ctx)
        tipo_id = (
            await session.execute(text("SELECT id FROM document_types WHERE code = 'PACKING_LIST'"))
        ).scalar_one()
        await service.abrir_requisito(
            session,
            shipment_id=shipment_id,
            requirement_type=RequirementType.DOCUMENT,
            title="Packing list",
            required_from="STAFF",
            actor_user_id=ctx["operaciones"],
            permisos=await _permisos(session, redis, ctx["operaciones"]),
            document_type_id=tipo_id,
        )

        pagina = await queries.listar_shipments(
            session,
            permisos=await _permisos(session, redis, ctx["operaciones"]),
            filtros=queries.FiltrosListado(),
            limite=50,
        )

        fila = pagina.items[0]
        assert fila.requisitos_abiertos == 1
        assert fila.requisitos_del_cliente == 0


class TestDashboard:
    async def test_las_tarjetas_cuentan_por_estado(self, session: AsyncSession, redis) -> None:
        ctx = await _entorno(session)
        await _carga(session, ctx, estado=ShipmentStatus.RECEIVED)
        await _carga(session, ctx, estado=ShipmentStatus.STORED)
        await _carga(session, ctx, estado=ShipmentStatus.IN_TRANSIT)

        tarjetas = await queries.tarjetas_dashboard(
            session,
            permisos=await _permisos(session, redis, ctx["operaciones"]),
            solo_del_cliente=False,
        )

        assert tarjetas.en_bodega == 2
        assert tarjetas.en_transito == 1

    async def test_proximos_a_llegar_usa_la_ventana(self, session: AsyncSession, redis) -> None:
        ctx = await _entorno(session)
        await _carga(
            session,
            ctx,
            estado=ShipmentStatus.IN_TRANSIT,
            eta=datetime.now(UTC) + timedelta(days=3),
        )
        await _carga(
            session,
            ctx,
            estado=ShipmentStatus.IN_TRANSIT,
            eta=datetime.now(UTC) + timedelta(days=90),
        )

        tarjetas = await queries.tarjetas_dashboard(
            session,
            permisos=await _permisos(session, redis, ctx["operaciones"]),
            solo_del_cliente=False,
        )

        assert tarjetas.proximos_a_llegar == 1

    async def test_una_carga_terminal_no_cuenta_como_proxima(
        self, session: AsyncSession, redis
    ) -> None:
        ctx = await _entorno(session)
        await _carga(
            session,
            ctx,
            estado=ShipmentStatus.DELIVERED,
            eta=datetime.now(UTC) + timedelta(days=2),
        )

        tarjetas = await queries.tarjetas_dashboard(
            session,
            permisos=await _permisos(session, redis, ctx["operaciones"]),
            solo_del_cliente=False,
        )

        assert tarjetas.proximos_a_llegar == 0

    async def test_entregados_este_mes(self, session: AsyncSession, redis) -> None:
        ctx = await _entorno(session)
        await _carga(session, ctx, estado=ShipmentStatus.DELIVERED, entregada_el=datetime.now(UTC))
        await _carga(
            session,
            ctx,
            estado=ShipmentStatus.DELIVERED,
            entregada_el=datetime.now(UTC) - timedelta(days=95),
        )

        tarjetas = await queries.tarjetas_dashboard(
            session,
            permisos=await _permisos(session, redis, ctx["operaciones"]),
            solo_del_cliente=False,
        )

        assert tarjetas.entregados_este_mes == 1

    async def test_requieren_accion_distingue_cliente_de_operaciones(
        self, session: AsyncSession, redis
    ) -> None:
        ctx = await _entorno(session)
        shipment_id = await _carga(session, ctx)
        tipo_id = (
            await session.execute(text("SELECT id FROM document_types WHERE code = 'PACKING_LIST'"))
        ).scalar_one()
        await service.abrir_requisito(
            session,
            shipment_id=shipment_id,
            requirement_type=RequirementType.DOCUMENT,
            title="Packing list",
            required_from="STAFF",
            actor_user_id=ctx["operaciones"],
            permisos=await _permisos(session, redis, ctx["operaciones"]),
            document_type_id=tipo_id,
        )
        permisos = await _permisos(session, redis, ctx["operaciones"])

        vista_cliente = await queries.tarjetas_dashboard(
            session, permisos=permisos, solo_del_cliente=True
        )
        vista_operaciones = await queries.tarjetas_dashboard(
            session, permisos=permisos, solo_del_cliente=False
        )

        assert vista_cliente.requieren_accion == 0
        assert vista_operaciones.requieren_accion == 1

    async def test_el_dashboard_respeta_el_aislamiento(self, session: AsyncSession, redis) -> None:
        ctx = await _entorno(session)
        await _carga(session, ctx, empresa="empresa_a", estado=ShipmentStatus.IN_TRANSIT)
        await _carga(session, ctx, empresa="empresa_b", estado=ShipmentStatus.IN_TRANSIT)

        tarjetas = await queries.tarjetas_dashboard(
            session,
            permisos=await _permisos(session, redis, ctx["cliente_a"]),
            solo_del_cliente=True,
        )

        assert tarjetas.en_transito == 1

    async def test_proximos_movimientos_ordena_por_llegada(
        self, session: AsyncSession, redis
    ) -> None:
        ctx = await _entorno(session)
        base = datetime.now(UTC)
        tarde = await _carga(
            session, ctx, estado=ShipmentStatus.IN_TRANSIT, eta=base + timedelta(days=10)
        )
        pronto = await _carga(
            session, ctx, estado=ShipmentStatus.IN_TRANSIT, eta=base + timedelta(days=1)
        )

        movimientos = await queries.proximos_movimientos(
            session, permisos=await _permisos(session, redis, ctx["operaciones"])
        )

        assert [m.id for m in movimientos] == [pronto, tarde]

    async def test_proximos_movimientos_trae_la_factura_como_referencia(
        self, session: AsyncSession, redis
    ) -> None:
        ctx = await _entorno(session)
        shipment_id = await _carga(
            session,
            ctx,
            estado=ShipmentStatus.IN_TRANSIT,
            eta=datetime.now(UTC) + timedelta(days=2),
        )
        await session.execute(
            text("""
                INSERT INTO shipment_references (shipment_id, reference_type, value)
                VALUES (:s, :t, 'FAC-777')
            """),
            {"s": shipment_id, "t": ReferenceType.INVOICE},
        )

        movimientos = await queries.proximos_movimientos(
            session, permisos=await _permisos(session, redis, ctx["operaciones"])
        )

        assert movimientos[0].factura == "FAC-777"
        assert movimientos[0].origen_pais == "US"
        assert movimientos[0].destino_pais == "CR"

    async def test_sin_factura_la_referencia_queda_nula(self, session: AsyncSession, redis) -> None:
        """El WR es opcional y la factura puede no existir todavía: la interfaz
        debe poder mostrar otra cosa, no romperse."""
        ctx = await _entorno(session)
        await _carga(
            session,
            ctx,
            estado=ShipmentStatus.IN_TRANSIT,
            eta=datetime.now(UTC) + timedelta(days=2),
        )

        movimientos = await queries.proximos_movimientos(
            session, permisos=await _permisos(session, redis, ctx["operaciones"])
        )

        assert movimientos[0].factura is None


class TestTimeline:
    async def test_el_timeline_pagina_con_cursor(self, session: AsyncSession, redis) -> None:
        ctx = await _entorno(session)
        shipment_id = await _carga(session, ctx)
        base = datetime.now(UTC)
        for i in range(7):
            await session.execute(
                text("""
                    INSERT INTO shipment_events (shipment_id, event_type, title, occurred_at)
                    VALUES (:s, 'NOTE', :t, :o)
                """),
                {"s": shipment_id, "t": f"Evento {i}", "o": base - timedelta(hours=i)},
            )
        permisos = await _permisos(session, redis, ctx["operaciones"])

        vistos: list[uuid.UUID] = []
        cursor = None
        while True:
            pagina = await queries.timeline(
                session,
                shipment_id=shipment_id,
                permisos=permisos,
                limite=3,
                cursor=cursor,
            )
            vistos.extend(f.id for f in pagina.items)
            if not pagina.has_more:
                break
            cursor = Cursor.decodificar(pagina.next_cursor)

        assert len(vistos) == 7
        assert len(set(vistos)) == 7

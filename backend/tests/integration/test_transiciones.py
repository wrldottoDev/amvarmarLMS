"""Motor de transiciones y requisitos (Paso 2.4)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from scripts.seed_document_types import sembrar as sembrar_documentos
from scripts.seed_rbac import sembrar as sembrar_rbac
from scripts.seed_shipment_statuses import sembrar as sembrar_estados
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.rbac.models import RoleCode, ScopeType
from app.modules.rbac.service import obtener_permisos_efectivos
from app.modules.shipments import service
from app.modules.shipments.catalog import ESTADOS, TRANSICIONES
from app.modules.shipments.models import (
    ReferenceType,
    RequirementStatus,
    RequirementType,
    ShipmentStatus,
)
from tests.piezas import sembrar_pieza

pytestmark = pytest.mark.integration


async def _entorno(session: AsyncSession, rol: str = RoleCode.OPS_ADMIN) -> dict[str, object]:
    await sembrar_rbac(session)
    await sembrar_estados(session)
    await sembrar_documentos(session)

    company_id = (
        await session.execute(
            text(
                "INSERT INTO companies (legal_name, status) "
                "VALUES ('Transiciones S.A.', 'ACTIVE') RETURNING id"
            )
        )
    ).scalar_one()
    user_id = (
        await session.execute(
            text("""
                INSERT INTO users (email, password_hash, first_name, last_name, status)
                VALUES (:e, 'h', 'N', 'A', 'ACTIVE') RETURNING id
            """),
            {"e": f"tr-{uuid.uuid4().hex[:8]}@amvarmar.com"},
        )
    ).scalar_one()

    scope = ScopeType.ORGANIZATION if rol.startswith("CLIENT") else ScopeType.GLOBAL
    await session.execute(
        text("""
            INSERT INTO user_role_assignments (user_id, role_id, scope_type, company_id)
            SELECT :u, r.id, :scope, :company FROM roles r WHERE r.code = :rol
        """),
        {
            "u": user_id,
            "rol": rol,
            "scope": scope,
            "company": company_id if scope == ScopeType.ORGANIZATION else None,
        },
    )
    if scope == ScopeType.ORGANIZATION:
        await session.execute(
            text("""
                INSERT INTO company_memberships (company_id, user_id, status)
                VALUES (:c, :u, 'ACTIVE')
            """),
            {"c": company_id, "u": user_id},
        )

    miami = await _ubicacion(session, "US", "MIA", "Miami")
    shanghai = await _ubicacion(session, "CN", "SHA", "Shanghái")
    destino = await _ubicacion(session, "CR", "SJO", "San José")

    bodega = (
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

    return {
        "company_id": company_id,
        "user_id": user_id,
        "miami": miami,
        "shanghai": shanghai,
        "destino": destino,
        "bodega": bodega,
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


async def _carga(
    session: AsyncSession,
    ctx: dict[str, object],
    *,
    estado: str = ShipmentStatus.PRE_ALERT,
    con_bodega_miami: bool = False,
) -> uuid.UUID:
    carga = (
        await session.execute(
            text("""
                INSERT INTO shipments
                    (company_id, created_by, current_status_code,
                     origin_location_id, origin_facility_id, destination_location_id)
                VALUES (:c, :u, :estado, :loc, :fac, :dest)
                RETURNING id
            """),
            {
                "c": ctx["company_id"],
                "u": ctx["user_id"],
                "estado": estado,
                "loc": ctx["miami"] if con_bodega_miami else ctx["shanghai"],
                "fac": ctx["bodega"] if con_bodega_miami else None,
                "dest": ctx["destino"],
            },
        )
    ).scalar_one()
    # Toda carga activa necesita al menos una pieza.
    await sembrar_pieza(session, carga)
    return carga


async def _permisos(session: AsyncSession, redis, ctx: dict[str, object]):
    return await obtener_permisos_efectivos(session, redis, ctx["user_id"])


async def _transicionar(
    session: AsyncSession,
    redis,
    ctx: dict[str, object],
    shipment_id: uuid.UUID,
    hacia: str,
    *,
    row_version: int = 1,
    note: str | None = None,
) -> service.ResultadoTransicion:
    return await service.transicionar(
        session,
        shipment_id=shipment_id,
        datos=service.DatosTransicion(to_status=hacia, row_version=row_version, note=note),
        actor_user_id=ctx["user_id"],
        permisos=await _permisos(session, redis, ctx),
    )


def _pares_validos() -> set[tuple[str, str]]:
    return {(str(t.desde), str(t.hacia)) for t in TRANSICIONES}


def _todos_los_pares() -> list[tuple[str, str, bool]]:
    """Los 9x9 pares posibles, marcando cuáles son válidos. 72 casos."""
    codigos = [str(c) for c in ESTADOS]
    validos = _pares_validos()
    return [
        (desde, hacia, (desde, hacia) in validos)
        for desde in codigos
        for hacia in codigos
        if desde != hacia
    ]


class TestMatrizCompleta:
    @pytest.mark.parametrize(("desde", "hacia", "es_valida"), _todos_los_pares())
    async def test_toda_combinacion_de_estados(
        self, session: AsyncSession, redis, desde: str, hacia: str, es_valida: bool
    ) -> None:
        """Los 72 pares posibles. Los que no están en el catálogo dan 409."""
        ctx = await _entorno(session, RoleCode.SUPER_ADMIN)
        # Miami exige WR antes de STORED; se usa origen sin bodega para que la
        # matriz pruebe el catálogo y no esa política.
        shipment_id = await _carga(session, ctx, estado=desde)

        if es_valida:
            resultado = await _transicionar(
                session, redis, ctx, shipment_id, hacia, note="motivo de prueba"
            )
            assert resultado.hacia == hacia
        else:
            with pytest.raises(service.TransicionInvalida) as error:
                await _transicionar(session, redis, ctx, shipment_id, hacia, note="x")

            assert error.value.code == "SHIPMENT_TRANSITION_INVALID"
            assert error.value.status_code == 409
            # El detalle dice qué SÍ se puede hacer desde el estado actual.
            assert error.value.details[0]["from"] == desde
            assert hacia not in error.value.details[0]["allowed"]


class TestPermisos:
    async def test_un_cliente_no_cambia_estados_operativos(
        self, session: AsyncSession, redis
    ) -> None:
        ctx = await _entorno(session, RoleCode.CLIENT_USER)
        shipment_id = await _carga(session, ctx)

        with pytest.raises(service.SinPermisoParaTransicion):
            await _transicionar(session, redis, ctx, shipment_id, ShipmentStatus.IN_TRANSIT)

    async def test_un_agente_no_revierte_una_entrega(self, session: AsyncSession, redis) -> None:
        """Solo SUPER_ADMIN, y el motor lo sabe por el permiso de la fila."""
        ctx = await _entorno(session, RoleCode.OPS_AGENT)
        shipment_id = await _carga(session, ctx, estado=ShipmentStatus.DELIVERED)

        with pytest.raises(service.SinPermisoParaTransicion):
            await _transicionar(
                session, redis, ctx, shipment_id, ShipmentStatus.DISPATCHED, note="error"
            )

    async def test_un_agente_no_cancela_en_transito(self, session: AsyncSession, redis) -> None:
        ctx = await _entorno(session, RoleCode.OPS_AGENT)
        shipment_id = await _carga(session, ctx, estado=ShipmentStatus.IN_TRANSIT)

        with pytest.raises(service.SinPermisoParaTransicion):
            await _transicionar(
                session, redis, ctx, shipment_id, ShipmentStatus.CANCELLED, note="motivo"
            )

    async def test_un_agente_si_cancela_una_prealerta(self, session: AsyncSession, redis) -> None:
        ctx = await _entorno(session, RoleCode.OPS_AGENT)
        shipment_id = await _carga(session, ctx, estado=ShipmentStatus.PRE_ALERT)

        resultado = await _transicionar(
            session, redis, ctx, shipment_id, ShipmentStatus.CANCELLED, note="Duplicada"
        )

        assert resultado.hacia == ShipmentStatus.CANCELLED

    async def test_un_cliente_de_otra_empresa_no_puede(self, session: AsyncSession, redis) -> None:
        """El permiso ORGANIZATION no alcanza a la carga de otra empresa."""
        ctx = await _entorno(session, RoleCode.OPS_ADMIN)
        otra_empresa = (
            await session.execute(
                text(
                    "INSERT INTO companies (legal_name, status) "
                    "VALUES ('Ajena S.A.', 'ACTIVE') RETURNING id"
                )
            )
        ).scalar_one()
        cliente = (
            await session.execute(
                text("""
                    INSERT INTO users (email, password_hash, first_name, last_name, status)
                    VALUES (:e, 'h', 'C', 'A', 'ACTIVE') RETURNING id
                """),
                {"e": f"ajeno-{uuid.uuid4().hex[:8]}@amvarmar.com"},
            )
        ).scalar_one()
        await session.execute(
            text("""
                INSERT INTO user_role_assignments (user_id, role_id, scope_type, company_id)
                SELECT :u, r.id, 'ORGANIZATION', :c FROM roles r WHERE r.code = 'CLIENT_ADMIN'
            """),
            {"u": cliente, "c": otra_empresa},
        )
        shipment_id = await _carga(session, ctx)

        permisos = await obtener_permisos_efectivos(session, redis, cliente)

        with pytest.raises(service.SinPermisoParaTransicion):
            await service.transicionar(
                session,
                shipment_id=shipment_id,
                datos=service.DatosTransicion(
                    to_status=ShipmentStatus.CANCELLED, row_version=1, note="x"
                ),
                actor_user_id=cliente,
                permisos=permisos,
            )


class TestTransicionesDisponibles:
    async def test_operaciones_recibe_solo_destinos_del_catalogo_y_su_permiso(
        self, session: AsyncSession, redis
    ) -> None:
        ctx = await _entorno(session, RoleCode.OPS_ADMIN)
        shipment_id = await _carga(session, ctx)

        opciones = await service.transiciones_disponibles(
            session,
            shipment_id=shipment_id,
            permisos=await _permisos(session, redis, ctx),
        )

        assert {o.to_status for o in opciones} == {"IN_TRANSIT", "CANCELLED"}
        cancelar = next(o for o in opciones if o.to_status == "CANCELLED")
        assert cancelar.requires_reason is True
        assert cancelar.blocked is False

    async def test_cliente_solo_recibe_cancelacion_de_su_prealerta(
        self, session: AsyncSession, redis
    ) -> None:
        ctx = await _entorno(session, RoleCode.CLIENT_USER)
        shipment_id = await _carga(session, ctx)

        opciones = await service.transiciones_disponibles(
            session,
            shipment_id=shipment_id,
            permisos=await _permisos(session, redis, ctx),
        )

        assert [o.to_status for o in opciones] == ["CANCELLED"]
        assert opciones[0].requires_reason is True

    async def test_muestra_el_wr_faltante_como_bloqueo_sin_ocultar_el_destino(
        self, session: AsyncSession, redis
    ) -> None:
        ctx = await _entorno(session, RoleCode.OPS_ADMIN)
        shipment_id = await _carga(
            session, ctx, estado=ShipmentStatus.RECEIVED, con_bodega_miami=True
        )

        opciones = await service.transiciones_disponibles(
            session,
            shipment_id=shipment_id,
            permisos=await _permisos(session, redis, ctx),
        )

        almacenar = next(o for o in opciones if o.to_status == "STORED")
        assert almacenar.blocked is True
        assert almacenar.blockers[0]["code"] == "SHIPMENT_MISSING_WR"


class TestMotivoYVersion:
    async def test_un_retroceso_exige_motivo(self, session: AsyncSession, redis) -> None:
        ctx = await _entorno(session, RoleCode.OPS_ADMIN)
        shipment_id = await _carga(session, ctx, estado=ShipmentStatus.IN_TRANSIT)

        with pytest.raises(service.MotivoRequerido):
            await _transicionar(session, redis, ctx, shipment_id, ShipmentStatus.PRE_ALERT)

    async def test_un_motivo_en_blanco_no_cuenta(self, session: AsyncSession, redis) -> None:
        ctx = await _entorno(session, RoleCode.OPS_ADMIN)
        shipment_id = await _carga(session, ctx, estado=ShipmentStatus.IN_TRANSIT)

        with pytest.raises(service.MotivoRequerido):
            await _transicionar(
                session, redis, ctx, shipment_id, ShipmentStatus.PRE_ALERT, note="   "
            )

    async def test_avanzar_no_exige_motivo(self, session: AsyncSession, redis) -> None:
        ctx = await _entorno(session, RoleCode.OPS_ADMIN)
        shipment_id = await _carga(session, ctx)

        resultado = await _transicionar(session, redis, ctx, shipment_id, ShipmentStatus.IN_TRANSIT)

        assert resultado.hacia == ShipmentStatus.IN_TRANSIT

    async def test_version_desactualizada_da_conflicto(self, session: AsyncSession, redis) -> None:
        ctx = await _entorno(session, RoleCode.OPS_ADMIN)
        shipment_id = await _carga(session, ctx)

        with pytest.raises(service.VersionDesactualizada) as error:
            await _transicionar(
                session, redis, ctx, shipment_id, ShipmentStatus.IN_TRANSIT, row_version=99
            )

        assert error.value.status_code == 409
        assert error.value.details[0]["row_version_actual"] == 1

    async def test_la_version_sube_con_cada_transicion(self, session: AsyncSession, redis) -> None:
        ctx = await _entorno(session, RoleCode.OPS_ADMIN)
        shipment_id = await _carga(session, ctx)

        primera = await _transicionar(session, redis, ctx, shipment_id, ShipmentStatus.IN_TRANSIT)
        segunda = await _transicionar(
            session,
            redis,
            ctx,
            shipment_id,
            ShipmentStatus.RECEIVED,
            row_version=primera.row_version,
        )

        assert primera.row_version == 2
        assert segunda.row_version == 3


class TestEfectosDeLaTransicion:
    async def test_deja_exactamente_un_evento(self, session: AsyncSession, redis) -> None:
        ctx = await _entorno(session, RoleCode.OPS_ADMIN)
        shipment_id = await _carga(session, ctx)

        await _transicionar(session, redis, ctx, shipment_id, ShipmentStatus.IN_TRANSIT)

        eventos = (
            await session.execute(
                text("""
                    SELECT from_status_code, to_status_code, event_type, actor_user_id
                    FROM shipment_events WHERE shipment_id = :s
                """),
                {"s": shipment_id},
            )
        ).all()

        assert len(eventos) == 1
        assert eventos[0].from_status_code == ShipmentStatus.PRE_ALERT
        assert eventos[0].to_status_code == ShipmentStatus.IN_TRANSIT
        assert eventos[0].actor_user_id == ctx["user_id"]

    async def test_graba_la_fecha_del_hito(self, session: AsyncSession, redis) -> None:
        """El expediente y su cronología no pueden divergir."""
        ctx = await _entorno(session, RoleCode.OPS_ADMIN)
        shipment_id = await _carga(session, ctx, estado=ShipmentStatus.IN_TRANSIT)

        await _transicionar(session, redis, ctx, shipment_id, ShipmentStatus.RECEIVED)

        received_at = (
            await session.execute(
                text("SELECT received_at FROM shipments WHERE id = :s"), {"s": shipment_id}
            )
        ).scalar_one()
        assert received_at is not None

    async def test_una_transicion_atrasada_usa_su_fecha_real(
        self, session: AsyncSession, redis
    ) -> None:
        ctx = await _entorno(session, RoleCode.OPS_ADMIN)
        shipment_id = await _carga(session, ctx, estado=ShipmentStatus.IN_TRANSIT)
        anteayer = datetime.now(UTC) - timedelta(days=2)

        await service.transicionar(
            session,
            shipment_id=shipment_id,
            datos=service.DatosTransicion(
                to_status=ShipmentStatus.RECEIVED, row_version=1, occurred_at=anteayer
            ),
            actor_user_id=ctx["user_id"],
            permisos=await _permisos(session, redis, ctx),
        )

        fila = (
            await session.execute(
                text("""
                    SELECT s.received_at, e.occurred_at, e.recorded_at
                    FROM shipments s JOIN shipment_events e ON e.shipment_id = s.id
                    WHERE s.id = :s AND e.event_type = 'STATUS_CHANGED'
                """),
                {"s": shipment_id},
            )
        ).one()
        assert fila.received_at.date() == anteayer.date()
        assert fila.occurred_at < fila.recorded_at


class TestRetencion:
    """ADR-0007: `retention_until` se calcula en la misma transacción que la
    transición a DELIVERED/CANCELLED — nunca al vuelo."""

    async def test_entrar_a_delivered_calcula_seis_meses_de_retencion(
        self, session: AsyncSession, redis
    ) -> None:
        ctx = await _entorno(session, RoleCode.OPS_ADMIN)
        shipment_id = await _carga(session, ctx, estado=ShipmentStatus.DISPATCHED)

        await _transicionar(session, redis, ctx, shipment_id, ShipmentStatus.DELIVERED)

        fila = (
            await session.execute(
                text("SELECT delivered_at, retention_until FROM shipments WHERE id = :s"),
                {"s": shipment_id},
            )
        ).one()
        assert fila.retention_until is not None
        diferencia = fila.retention_until - fila.delivered_at
        assert timedelta(days=175) < diferencia < timedelta(days=190)

    async def test_entrar_a_cancelled_tambien_calcula_retencion(
        self, session: AsyncSession, redis
    ) -> None:
        ctx = await _entorno(session, RoleCode.OPS_ADMIN)
        shipment_id = await _carga(session, ctx, estado=ShipmentStatus.PRE_ALERT)

        await _transicionar(
            session, redis, ctx, shipment_id, ShipmentStatus.CANCELLED, note="motivo"
        )

        retention_until = (
            await session.execute(
                text("SELECT retention_until FROM shipments WHERE id = :s"), {"s": shipment_id}
            )
        ).scalar_one()
        assert retention_until is not None

    async def test_revertir_una_entrega_borra_la_retencion(
        self, session: AsyncSession, redis
    ) -> None:
        """La carga vuelve a estar abierta: `retention_until` ya no aplica."""
        ctx = await _entorno(session, RoleCode.SUPER_ADMIN)
        shipment_id = await _carga(session, ctx, estado=ShipmentStatus.DELIVERED)

        await _transicionar(
            session, redis, ctx, shipment_id, ShipmentStatus.DISPATCHED, note="error"
        )

        retention_until = (
            await session.execute(
                text("SELECT retention_until FROM shipments WHERE id = :s"), {"s": shipment_id}
            )
        ).scalar_one()
        assert retention_until is None

    async def test_una_transicion_normal_no_toca_la_retencion(
        self, session: AsyncSession, redis
    ) -> None:
        ctx = await _entorno(session, RoleCode.OPS_ADMIN)
        shipment_id = await _carga(session, ctx)

        await _transicionar(session, redis, ctx, shipment_id, ShipmentStatus.IN_TRANSIT)

        retention_until = (
            await session.execute(
                text("SELECT retention_until FROM shipments WHERE id = :s"), {"s": shipment_id}
            )
        ).scalar_one()
        assert retention_until is None


class TestPoliticasDeDominio:
    async def test_miami_no_almacena_sin_wr(self, session: AsyncSession, redis) -> None:
        """ADR-0005, aplicado desde el motor de transiciones."""
        ctx = await _entorno(session, RoleCode.OPS_ADMIN)
        shipment_id = await _carga(
            session, ctx, estado=ShipmentStatus.RECEIVED, con_bodega_miami=True
        )

        with pytest.raises(service.ReglaDeNegocioViolada) as error:
            await _transicionar(session, redis, ctx, shipment_id, ShipmentStatus.STORED)

        assert error.value.code == "SHIPMENT_MISSING_WR"

    async def test_con_wr_miami_si_almacena(self, session: AsyncSession, redis) -> None:
        ctx = await _entorno(session, RoleCode.OPS_ADMIN)
        shipment_id = await _carga(
            session, ctx, estado=ShipmentStatus.RECEIVED, con_bodega_miami=True
        )
        await session.execute(
            text("""
                INSERT INTO shipment_references (shipment_id, reference_type, value)
                VALUES (:s, :t, 'WR000501')
            """),
            {"s": shipment_id, "t": ReferenceType.WR},
        )

        resultado = await _transicionar(session, redis, ctx, shipment_id, ShipmentStatus.STORED)

        assert resultado.hacia == ShipmentStatus.STORED

    async def test_un_origen_sin_bodega_almacena_sin_wr(self, session: AsyncSession, redis) -> None:
        ctx = await _entorno(session, RoleCode.OPS_ADMIN)
        shipment_id = await _carga(session, ctx, estado=ShipmentStatus.RECEIVED)

        resultado = await _transicionar(session, redis, ctx, shipment_id, ShipmentStatus.STORED)

        assert resultado.hacia == ShipmentStatus.STORED


class TestRequisitos:
    async def test_un_requisito_abierto_no_bloquea_una_transicion_ajena(
        self, session: AsyncSession, redis
    ) -> None:
        """El concepto central: "faltan documentos" NO es un estado.

        La carga avanza a IN_TRANSIT con un requisito abierto; solo el despacho
        se bloquea.
        """
        ctx = await _entorno(session, RoleCode.OPS_ADMIN)
        shipment_id = await _carga(session, ctx)
        await _abrir_documental(session, ctx, shipment_id, redis)

        resultado = await _transicionar(session, redis, ctx, shipment_id, ShipmentStatus.IN_TRANSIT)

        assert resultado.hacia == ShipmentStatus.IN_TRANSIT
        pendientes = (
            await session.execute(
                text("""
                    SELECT count(*) FROM shipment_requirements
                    WHERE shipment_id = :s AND status = 'PENDING'
                """),
                {"s": shipment_id},
            )
        ).scalar_one()
        assert pendientes == 1

    async def test_no_se_despacha_con_requisitos_bloqueantes(
        self, session: AsyncSession, redis
    ) -> None:
        ctx = await _entorno(session, RoleCode.OPS_ADMIN)
        shipment_id = await _carga(session, ctx, estado=ShipmentStatus.PREPARING)
        await _abrir_documental(session, ctx, shipment_id, redis)

        with pytest.raises(service.RequisitosPendientes) as error:
            await _transicionar(session, redis, ctx, shipment_id, ShipmentStatus.DISPATCHED)

        assert error.value.code == "SHIPMENT_REQUIREMENTS_PENDING"
        assert error.value.details[0]["estado"] == RequirementStatus.PENDING

    async def test_un_requisito_informativo_no_impide_despachar(
        self, session: AsyncSession, redis
    ) -> None:
        ctx = await _entorno(session, RoleCode.OPS_ADMIN)
        shipment_id = await _carga(session, ctx, estado=ShipmentStatus.PREPARING)
        await service.abrir_requisito(
            session,
            shipment_id=shipment_id,
            requirement_type=RequirementType.INFORMATION,
            title="Confirmar horario de recepción",
            required_from="CLIENT",
            actor_user_id=ctx["user_id"],
            permisos=await _permisos(session, redis, ctx),
            blocks_dispatch=False,
        )

        resultado = await _transicionar(session, redis, ctx, shipment_id, ShipmentStatus.DISPATCHED)

        assert resultado.hacia == ShipmentStatus.DISPATCHED

    async def test_verificar_el_documento_desbloquea_el_despacho(
        self, session: AsyncSession, redis
    ) -> None:
        ctx = await _entorno(session, RoleCode.OPS_ADMIN)
        shipment_id = await _carga(session, ctx, estado=ShipmentStatus.PREPARING)
        requirement_id = await _abrir_documental(session, ctx, shipment_id, redis)
        document_id = await _documento_listo(session, ctx, shipment_id)

        await service.verificar_requisito_documental(
            session,
            shipment_id=shipment_id,
            requirement_id=requirement_id,
            document_id=document_id,
            actor_user_id=ctx["user_id"],
            permisos=await _permisos(session, redis, ctx),
        )

        resultado = await _transicionar(session, redis, ctx, shipment_id, ShipmentStatus.DISPATCHED)
        assert resultado.hacia == ShipmentStatus.DISPATCHED

    async def test_subir_el_archivo_no_basta(self, session: AsyncSession, redis) -> None:
        """`UPLOADED` no satisface: Operaciones tiene que verificarlo (ADR-0003)."""
        ctx = await _entorno(session, RoleCode.OPS_ADMIN)
        shipment_id = await _carga(session, ctx, estado=ShipmentStatus.PREPARING)
        requirement_id = await _abrir_documental(session, ctx, shipment_id, redis)
        await service.resolver_requisito(
            session,
            requirement_id=requirement_id,
            nuevo_estado=RequirementStatus.UPLOADED,
            actor_user_id=ctx["user_id"],
            permisos=await _permisos(session, redis, ctx),
        )

        with pytest.raises(service.RequisitosPendientes):
            await _transicionar(session, redis, ctx, shipment_id, ShipmentStatus.DISPATCHED)

    async def test_exonerar_sin_motivo_falla(self, session: AsyncSession, redis) -> None:
        ctx = await _entorno(session, RoleCode.OPS_ADMIN)
        shipment_id = await _carga(session, ctx)
        requirement_id = await _abrir_documental(session, ctx, shipment_id, redis)

        with pytest.raises(service.MotivoRequerido):
            await service.resolver_requisito(
                session,
                requirement_id=requirement_id,
                nuevo_estado=RequirementStatus.WAIVED,
                actor_user_id=ctx["user_id"],
                permisos=await _permisos(session, redis, ctx),
            )

    async def test_exonerar_con_motivo_desbloquea(self, session: AsyncSession, redis) -> None:
        ctx = await _entorno(session, RoleCode.OPS_ADMIN)
        shipment_id = await _carga(session, ctx, estado=ShipmentStatus.PREPARING)
        requirement_id = await _abrir_documental(session, ctx, shipment_id, redis)

        await service.resolver_requisito(
            session,
            requirement_id=requirement_id,
            nuevo_estado=RequirementStatus.WAIVED,
            actor_user_id=ctx["user_id"],
            permisos=await _permisos(session, redis, ctx),
            motivo="El proveedor entregó la factura directo a AMVARMAR.",
        )

        resultado = await _transicionar(session, redis, ctx, shipment_id, ShipmentStatus.DISPATCHED)
        assert resultado.hacia == ShipmentStatus.DISPATCHED

    async def test_rechazar_sin_motivo_falla(self, session: AsyncSession, redis) -> None:
        ctx = await _entorno(session, RoleCode.OPS_ADMIN)
        shipment_id = await _carga(session, ctx)
        requirement_id = await _abrir_documental(session, ctx, shipment_id, redis)
        document_id = await _documento_listo(session, ctx, shipment_id)

        with pytest.raises(service.MotivoRequerido):
            await service.rechazar_requisito_documental(
                session,
                shipment_id=shipment_id,
                requirement_id=requirement_id,
                document_id=document_id,
                actor_user_id=ctx["user_id"],
                permisos=await _permisos(session, redis, ctx),
                motivo="",
            )

    async def test_un_requisito_de_pago_no_admite_estados_documentales(
        self, session: AsyncSession
    ) -> None:
        """El CHECK impide que un pago quede en VERIFIED, que ahí no significa nada."""
        ctx = await _entorno(session)
        shipment_id = await _carga(session, ctx)

        with pytest.raises(IntegrityError):
            await session.execute(
                text("""
                    INSERT INTO shipment_requirements
                        (shipment_id, requirement_type, title, required_from, status, created_by)
                    VALUES (:s, 'PAYMENT', 'Pago pendiente', 'CLIENT', 'VERIFIED', :u)
                """),
                {"s": shipment_id, "u": ctx["user_id"]},
            )

    async def test_un_requisito_documental_exige_tipo_de_documento(
        self, session: AsyncSession
    ) -> None:
        ctx = await _entorno(session)
        shipment_id = await _carga(session, ctx)

        with pytest.raises(IntegrityError):
            await session.execute(
                text("""
                    INSERT INTO shipment_requirements
                        (shipment_id, requirement_type, title, required_from, status, created_by)
                    VALUES (:s, 'DOCUMENT', 'Algo', 'CLIENT', 'PENDING', :u)
                """),
                {"s": shipment_id, "u": ctx["user_id"]},
            )


async def _documento_listo(
    session: AsyncSession, ctx: dict[str, object], shipment_id: uuid.UUID
) -> uuid.UUID:
    """Crea evidencia READY del requisito de factura comercial ya abierto."""
    document_id = (
        await session.execute(
            text("""
                INSERT INTO documents
                    (company_id, uploaded_by, storage_provider, storage_key,
                     original_name, safe_name, media_type, size_bytes, sha256,
                     upload_status, issued_by)
                VALUES (:c, :u, 's3', :key, 'factura.pdf', 'factura.pdf',
                        'application/pdf', 10, :sha, 'READY', 'PROVIDER')
                RETURNING id
            """),
            {
                "c": ctx["company_id"],
                "u": ctx["user_id"],
                "key": f"test/{uuid.uuid4()}.pdf",
                "sha": "a" * 64,
            },
        )
    ).scalar_one()
    tipo_id = (
        await session.execute(
            text("SELECT id FROM document_types WHERE code = 'COMMERCIAL_INVOICE'")
        )
    ).scalar_one()
    await session.execute(
        text("""
            INSERT INTO shipment_documents (shipment_id, document_id, document_type_id)
            VALUES (:s, :d, :t)
        """),
        {"s": shipment_id, "d": document_id, "t": tipo_id},
    )
    await session.execute(
        text("""
            UPDATE shipment_requirements SET status = 'UPLOADED'
            WHERE shipment_id = :s AND document_type_id = :t
        """),
        {"s": shipment_id, "t": tipo_id},
    )
    document: uuid.UUID = document_id
    return document


async def _abrir_documental(
    session: AsyncSession, ctx: dict[str, object], shipment_id: uuid.UUID, redis
) -> uuid.UUID:
    tipo_id = (
        await session.execute(
            text("SELECT id FROM document_types WHERE code = 'COMMERCIAL_INVOICE'")
        )
    ).scalar_one()
    return await service.abrir_requisito(
        session,
        shipment_id=shipment_id,
        requirement_type=RequirementType.DOCUMENT,
        title="Factura comercial",
        required_from="CLIENT",
        actor_user_id=ctx["user_id"],
        permisos=await _permisos(session, redis, ctx),
        document_type_id=tipo_id,
    )


class TestRollbackTransaccional:
    async def test_si_falla_el_evento_no_queda_el_estado_cambiado(
        self, session: AsyncSession, redis
    ) -> None:
        """Prueba del gate.

        Se fuerza el fallo de la inserción del evento con un trigger temporal.
        Sin atomicidad quedaría una carga en el estado nuevo sin registro de
        cómo llegó ahí — precisamente lo que la línea de tiempo existe para
        impedir.
        """
        ctx = await _entorno(session, RoleCode.OPS_ADMIN)
        shipment_id = await _carga(session, ctx)

        await session.execute(
            text("""
                CREATE OR REPLACE FUNCTION fallo_simulado() RETURNS trigger AS $$
                BEGIN
                    RAISE EXCEPTION 'fallo simulado al escribir el evento';
                END;
                $$ LANGUAGE plpgsql;
            """)
        )
        await session.execute(
            text("""
                CREATE TRIGGER trg_fallo_simulado
                BEFORE INSERT ON shipment_events
                FOR EACH ROW EXECUTE FUNCTION fallo_simulado();
            """)
        )
        # SAVEPOINT: el fallo revierte hasta aquí sin tumbar la sesión del test.
        punto = await session.begin_nested()

        with pytest.raises(Exception, match="fallo simulado"):
            await _transicionar(session, redis, ctx, shipment_id, ShipmentStatus.IN_TRANSIT)

        await punto.rollback()
        await session.execute(text("DROP TRIGGER trg_fallo_simulado ON shipment_events"))

        estado = (
            await session.execute(
                text("SELECT current_status_code, row_version FROM shipments WHERE id = :s"),
                {"s": shipment_id},
            )
        ).one()
        assert estado.current_status_code == ShipmentStatus.PRE_ALERT
        assert estado.row_version == 1

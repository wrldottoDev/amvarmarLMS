"""Inconformidad de entrega (ADR-0006): reportar y resolver.

Reportar NO cambia el estado por sí solo. Resolver con `RESOLVED_REVERTED`
dispara la reversión real vía el motor de transiciones (`service.transicionar`),
así que hereda su propia revalidación de permiso — no se duplica acá.
"""

import uuid

import pytest
from scripts.seed_rbac import sembrar as sembrar_rbac
from scripts.seed_shipment_statuses import sembrar as sembrar_estados
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import RecursoNoEncontrado, ReglaDeNegocioViolada, SinPermiso
from app.modules.rbac.models import RoleCode, ScopeType
from app.modules.rbac.service import obtener_permisos_efectivos
from app.modules.shipments import service
from app.modules.shipments.models import ShipmentStatus
from app.modules.shipments.service import (
    CargaArchivada,
    MotivoRequerido,
    SinPermisoParaTransicion,
)
from tests.piezas import sembrar_pieza

pytestmark = pytest.mark.integration


async def _entorno(session: AsyncSession) -> dict[str, object]:
    await sembrar_rbac(session)
    await sembrar_estados(session)

    empresa_a = await _empresa(session, "Disputas A")
    empresa_b = await _empresa(session, "Disputas B")
    cliente = await _usuario_con_rol(
        session, RoleCode.CLIENT_ADMIN, ScopeType.ORGANIZATION, empresa_a
    )
    ops_admin = await _usuario_con_rol(session, RoleCode.OPS_ADMIN, ScopeType.GLOBAL, None)
    super_admin = await _usuario_con_rol(session, RoleCode.SUPER_ADMIN, ScopeType.GLOBAL, None)
    origen = await _ubicacion(session, "US", "MIA", "Miami")
    destino = await _ubicacion(session, "CR", "SJO", "San José")

    return {
        "empresa_a": empresa_a,
        "empresa_b": empresa_b,
        "cliente": cliente,
        "ops_admin": ops_admin,
        "super_admin": super_admin,
        "origen": origen,
        "destino": destino,
    }


async def _empresa(session: AsyncSession, nombre: str) -> uuid.UUID:
    return (
        await session.execute(
            text("INSERT INTO companies (legal_name, status) VALUES (:n,'ACTIVE') RETURNING id"),
            {"n": f"{nombre} {uuid.uuid4().hex[:6]} S.A."},
        )
    ).scalar_one()


async def _usuario_con_rol(
    session: AsyncSession, rol: str, scope: str, empresa: uuid.UUID | None
) -> uuid.UUID:
    user_id = (
        await session.execute(
            text("""
                INSERT INTO users (email, password_hash, first_name, last_name, status)
                VALUES (:e,'h','N','A','ACTIVE') RETURNING id
            """),
            {"e": f"disp-{uuid.uuid4().hex[:10]}@amvarmar.com"},
        )
    ).scalar_one()
    await session.execute(
        text("""
            INSERT INTO user_role_assignments (user_id, role_id, scope_type, company_id)
            SELECT :u, r.id, :scope, :c FROM roles r WHERE r.code = :rol
        """),
        {"u": user_id, "rol": rol, "scope": scope, "c": empresa},
    )
    if empresa is not None:
        await session.execute(
            text(
                "INSERT INTO company_memberships (company_id, user_id, status) "
                "VALUES (:c,:u,'ACTIVE')"
            ),
            {"c": empresa, "u": user_id},
        )
    return user_id


async def _ubicacion(session: AsyncSession, pais: str, ciudad: str, nombre: str) -> uuid.UUID:
    return (
        await session.execute(
            text("""
                INSERT INTO locations (country_code, city_code, location_code, name)
                VALUES (:p,:c,:cod,:n)
                ON CONFLICT (location_code) DO UPDATE SET name = EXCLUDED.name
                RETURNING id
            """),
            {"p": pais, "c": ciudad, "cod": f"{pais}-{ciudad}", "n": nombre},
        )
    ).scalar_one()


async def _carga(
    session: AsyncSession, ctx: dict[str, object], *, estado: str = ShipmentStatus.DELIVERED
) -> uuid.UUID:
    shipment_id = (
        await session.execute(
            text("""
                INSERT INTO shipments
                    (company_id, created_by, current_status_code,
                     origin_location_id, destination_location_id)
                VALUES (:c,:u,:estado,:o,:d)
                RETURNING id
            """),
            {
                "c": ctx["empresa_a"],
                "u": ctx["cliente"],
                "estado": estado,
                "o": ctx["origen"],
                "d": ctx["destino"],
            },
        )
    ).scalar_one()
    await sembrar_pieza(session, shipment_id)
    return shipment_id


async def _permisos(session: AsyncSession, redis, user_id: uuid.UUID):
    return await obtener_permisos_efectivos(session, redis, user_id)


class TestReportarInconformidad:
    async def test_cliente_reporta_sobre_una_carga_entregada(
        self, session: AsyncSession, redis
    ) -> None:
        ctx = await _entorno(session)
        shipment_id = await _carga(session, ctx)
        permisos = await _permisos(session, redis, ctx["cliente"])

        dispute_id = await service.reportar_inconformidad(
            session,
            shipment_id=shipment_id,
            reason="La caja llegó vacía.",
            actor_user_id=ctx["cliente"],
            permisos=permisos,
        )

        fila = (
            await session.execute(
                text("SELECT status, reason FROM delivery_disputes WHERE id = :id"),
                {"id": dispute_id},
            )
        ).one()
        assert fila.status == "OPEN"
        assert fila.reason == "La caja llegó vacía."

        evento = (
            await session.execute(
                text(
                    "SELECT event_type FROM shipment_events "
                    "WHERE shipment_id = :s ORDER BY occurred_at DESC LIMIT 1"
                ),
                {"s": shipment_id},
            )
        ).scalar_one()
        assert evento == "DELIVERY_DISPUTED"

    async def test_no_se_puede_reportar_sobre_una_carga_no_entregada(
        self, session: AsyncSession, redis
    ) -> None:
        ctx = await _entorno(session)
        shipment_id = await _carga(session, ctx, estado=ShipmentStatus.STORED)
        permisos = await _permisos(session, redis, ctx["cliente"])

        with pytest.raises(ReglaDeNegocioViolada):
            await service.reportar_inconformidad(
                session,
                shipment_id=shipment_id,
                reason="No debería poder.",
                actor_user_id=ctx["cliente"],
                permisos=permisos,
            )

    async def test_sin_permiso_sobre_la_empresa_de_la_carga(
        self, session: AsyncSession, redis
    ) -> None:
        ctx = await _entorno(session)
        shipment_id = await _carga(session, ctx)
        # Un usuario de OTRA empresa, sin alcance sobre esta carga.
        ajeno = await _usuario_con_rol(
            session, RoleCode.CLIENT_ADMIN, ScopeType.ORGANIZATION, ctx["empresa_b"]
        )
        permisos = await _permisos(session, redis, ajeno)

        with pytest.raises(SinPermiso):
            await service.reportar_inconformidad(
                session,
                shipment_id=shipment_id,
                reason="No debería poder.",
                actor_user_id=ajeno,
                permisos=permisos,
            )

    async def test_motivo_vacio_se_rechaza(self, session: AsyncSession, redis) -> None:
        ctx = await _entorno(session)
        shipment_id = await _carga(session, ctx)
        permisos = await _permisos(session, redis, ctx["cliente"])

        with pytest.raises(MotivoRequerido):
            await service.reportar_inconformidad(
                session,
                shipment_id=shipment_id,
                reason="   ",
                actor_user_id=ctx["cliente"],
                permisos=permisos,
            )

    async def test_no_se_puede_reportar_sobre_una_carga_ya_archivada(
        self, session: AsyncSession, redis
    ) -> None:
        """ADR-0007: si nunca hubo disputa mientras la carga estaba abierta,
        el archivado cierra esa ventana — no se reabre después."""
        ctx = await _entorno(session)
        shipment_id = await _carga(session, ctx)
        await session.execute(
            text("UPDATE shipments SET archived_at = now() WHERE id = :id"), {"id": shipment_id}
        )
        permisos = await _permisos(session, redis, ctx["cliente"])

        with pytest.raises(CargaArchivada):
            await service.reportar_inconformidad(
                session,
                shipment_id=shipment_id,
                reason="Llegó tarde, me di cuenta ahora.",
                actor_user_id=ctx["cliente"],
                permisos=permisos,
            )

    async def test_no_se_puede_reportar_dos_inconformidades_abiertas_a_la_vez(
        self, session: AsyncSession, redis
    ) -> None:
        ctx = await _entorno(session)
        shipment_id = await _carga(session, ctx)
        permisos = await _permisos(session, redis, ctx["cliente"])
        await service.reportar_inconformidad(
            session,
            shipment_id=shipment_id,
            reason="Primera.",
            actor_user_id=ctx["cliente"],
            permisos=permisos,
        )

        with pytest.raises(Exception, match="Ya hay una inconformidad abierta"):
            await service.reportar_inconformidad(
                session,
                shipment_id=shipment_id,
                reason="Segunda.",
                actor_user_id=ctx["cliente"],
                permisos=permisos,
            )


class TestResolverInconformidad:
    async def _reportar(self, session: AsyncSession, redis, ctx: dict[str, object]) -> uuid.UUID:
        shipment_id = await _carga(session, ctx)
        permisos = await _permisos(session, redis, ctx["cliente"])
        return await service.reportar_inconformidad(
            session,
            shipment_id=shipment_id,
            reason="No reconozco la entrega.",
            actor_user_id=ctx["cliente"],
            permisos=permisos,
        )

    async def test_resolved_confirmed_no_toca_el_estado_de_la_carga(
        self, session: AsyncSession, redis
    ) -> None:
        ctx = await _entorno(session)
        dispute_id = await self._reportar(session, redis, ctx)
        permisos_ops = await _permisos(session, redis, ctx["ops_admin"])

        shipment_id = await service.resolver_inconformidad(
            session,
            dispute_id=dispute_id,
            nuevo_estado="RESOLVED_CONFIRMED",
            actor_user_id=ctx["ops_admin"],
            permisos=permisos_ops,
        )

        estado_carga = (
            await session.execute(
                text("SELECT current_status_code FROM shipments WHERE id = :id"),
                {"id": shipment_id},
            )
        ).scalar_one()
        assert estado_carga == ShipmentStatus.DELIVERED
        fila = (
            await session.execute(
                text("SELECT status, resolved_by_user_id FROM delivery_disputes WHERE id = :id"),
                {"id": dispute_id},
            )
        ).one()
        assert fila.status == "RESOLVED_CONFIRMED"
        assert fila.resolved_by_user_id == ctx["ops_admin"]

    async def test_resolved_reverted_por_super_admin_revierte_la_entrega(
        self, session: AsyncSession, redis
    ) -> None:
        ctx = await _entorno(session)
        dispute_id = await self._reportar(session, redis, ctx)
        permisos_super = await _permisos(session, redis, ctx["super_admin"])

        shipment_id = await service.resolver_inconformidad(
            session,
            dispute_id=dispute_id,
            nuevo_estado="RESOLVED_REVERTED",
            actor_user_id=ctx["super_admin"],
            permisos=permisos_super,
            motivo="Se confirma que no llegó.",
        )

        estado_carga = (
            await session.execute(
                text("SELECT current_status_code FROM shipments WHERE id = :id"),
                {"id": shipment_id},
            )
        ).scalar_one()
        assert estado_carga == ShipmentStatus.DISPATCHED

    async def test_resolved_reverted_por_ops_admin_falla_controlado(
        self, session: AsyncSession, redis
    ) -> None:
        """Revertir DELIVERED es exclusivo de SUPER_ADMIN — no cambia porque
        el pedido venga de una inconformidad en vez de un PATCH directo."""
        ctx = await _entorno(session)
        dispute_id = await self._reportar(session, redis, ctx)
        permisos_ops = await _permisos(session, redis, ctx["ops_admin"])

        with pytest.raises(SinPermisoParaTransicion):
            await service.resolver_inconformidad(
                session,
                dispute_id=dispute_id,
                nuevo_estado="RESOLVED_REVERTED",
                actor_user_id=ctx["ops_admin"],
                permisos=permisos_ops,
                motivo="No debería poder.",
            )

        fila = (
            await session.execute(
                text("SELECT status FROM delivery_disputes WHERE id = :id"), {"id": dispute_id}
            )
        ).scalar_one()
        assert fila == "OPEN"

    async def test_reverted_sin_motivo_se_rechaza(self, session: AsyncSession, redis) -> None:
        ctx = await _entorno(session)
        dispute_id = await self._reportar(session, redis, ctx)
        permisos_super = await _permisos(session, redis, ctx["super_admin"])

        with pytest.raises(MotivoRequerido):
            await service.resolver_inconformidad(
                session,
                dispute_id=dispute_id,
                nuevo_estado="RESOLVED_REVERTED",
                actor_user_id=ctx["super_admin"],
                permisos=permisos_super,
            )

    async def test_no_se_puede_resolver_dos_veces(self, session: AsyncSession, redis) -> None:
        ctx = await _entorno(session)
        dispute_id = await self._reportar(session, redis, ctx)
        permisos_ops = await _permisos(session, redis, ctx["ops_admin"])
        await service.resolver_inconformidad(
            session,
            dispute_id=dispute_id,
            nuevo_estado="RESOLVED_CONFIRMED",
            actor_user_id=ctx["ops_admin"],
            permisos=permisos_ops,
        )

        with pytest.raises(Exception, match="ya está"):
            await service.resolver_inconformidad(
                session,
                dispute_id=dispute_id,
                nuevo_estado="RESOLVED_CONFIRMED",
                actor_user_id=ctx["ops_admin"],
                permisos=permisos_ops,
            )

    async def test_inconformidad_inexistente_da_recurso_no_encontrado(
        self, session: AsyncSession, redis
    ) -> None:
        ctx = await _entorno(session)
        permisos_ops = await _permisos(session, redis, ctx["ops_admin"])

        with pytest.raises(RecursoNoEncontrado):
            await service.resolver_inconformidad(
                session,
                dispute_id=uuid.uuid4(),
                nuevo_estado="RESOLVED_CONFIRMED",
                actor_user_id=ctx["ops_admin"],
                permisos=permisos_ops,
            )

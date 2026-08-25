"""Catálogo de estados y grafo de transiciones (Paso 2.1)."""

import pytest
from scripts.seed_rbac import sembrar as sembrar_rbac
from scripts.seed_shipment_statuses import PermisoFaltante
from scripts.seed_shipment_statuses import sembrar as sembrar_estados
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.rbac.catalog import Perm
from app.modules.shipments.catalog import ESTADOS, TRANSICIONES
from app.modules.shipments.models import ShipmentStatus as S

pytestmark = pytest.mark.integration


@pytest.fixture
async def sembrado(session: AsyncSession) -> AsyncSession:
    await sembrar_rbac(session)
    await sembrar_estados(session)
    return session


class TestSeed:
    async def test_es_idempotente(self, session: AsyncSession) -> None:
        await sembrar_rbac(session)
        conteos = [await sembrar_estados(session) for _ in range(3)]

        assert conteos[0] == conteos[1] == conteos[2]
        assert conteos[0]["shipment_statuses"] == len(ESTADOS)
        assert conteos[0]["shipment_status_transitions"] == len(TRANSICIONES)

    async def test_falla_claro_si_faltan_los_permisos(self, session: AsyncSession) -> None:
        """Sin el seed de RBAC, el INSERT ... SELECT no insertaría nada y la
        transición quedaría sin declarar en silencio.

        La precondición se establece explícitamente: otros tests confirman
        permisos con sesiones propias, así que no se puede asumir que la tabla
        esté vacía. El borrado vive en la transacción de este test y se revierte
        al terminar.
        """
        await session.execute(text("DELETE FROM shipment_status_transitions"))
        await session.execute(text("DELETE FROM role_permissions"))
        await session.execute(text("DELETE FROM permissions"))

        with pytest.raises(PermisoFaltante):
            await sembrar_estados(session)

    async def test_retira_transiciones_que_salen_del_catalogo(self, sembrado: AsyncSession) -> None:
        await sembrado.execute(
            text("""
                INSERT INTO shipment_status_transitions
                    (from_status_code, to_status_code, required_permission_id)
                SELECT 'PRE_ALERT', 'DELIVERED', p.id
                FROM permissions p WHERE p.code = :perm
            """),
            {"perm": Perm.SHIPMENTS_TRANSITION_FORWARD},
        )

        await sembrar_estados(sembrado)

        sigue = (
            await sembrado.execute(
                text("""
                    SELECT count(*) FROM shipment_status_transitions
                    WHERE from_status_code = 'PRE_ALERT' AND to_status_code = 'DELIVERED'
                """)
            )
        ).scalar_one()
        assert sigue == 0


class TestGrafo:
    async def test_todo_estado_es_alcanzable_desde_prealerta(self, sembrado: AsyncSession) -> None:
        """Un estado inalcanzable sería un error de catálogo invisible."""
        aristas = (
            await sembrado.execute(
                text("""
                    SELECT from_status_code, to_status_code
                    FROM shipment_status_transitions WHERE is_active
                """)
            )
        ).all()

        vecinos: dict[str, list[str]] = {}
        for desde, hacia in aristas:
            vecinos.setdefault(desde, []).append(hacia)

        visitados = {str(S.PRE_ALERT)}
        pendientes = [str(S.PRE_ALERT)]
        while pendientes:
            actual = pendientes.pop()
            for siguiente in vecinos.get(actual, []):
                if siguiente not in visitados:
                    visitados.add(siguiente)
                    pendientes.append(siguiente)

        assert visitados == {str(code) for code in ESTADOS}

    async def test_no_hay_transiciones_huerfanas(self, sembrado: AsyncSession) -> None:
        huerfanas = (
            await sembrado.execute(
                text("""
                    SELECT count(*) FROM shipment_status_transitions t
                    WHERE NOT EXISTS (SELECT 1 FROM shipment_statuses s WHERE s.code = t.from_status_code)
                       OR NOT EXISTS (SELECT 1 FROM shipment_statuses s WHERE s.code = t.to_status_code)
                """)
            )
        ).scalar_one()

        assert huerfanas == 0

    async def test_una_transicion_no_puede_ir_al_mismo_estado(self, sembrado: AsyncSession) -> None:
        with pytest.raises(IntegrityError):
            await sembrado.execute(
                text("""
                    INSERT INTO shipment_status_transitions
                        (from_status_code, to_status_code, required_permission_id)
                    SELECT 'STORED', 'STORED', p.id FROM permissions p WHERE p.code = :perm
                """),
                {"perm": Perm.SHIPMENTS_TRANSITION_FORWARD},
            )

    async def test_solo_super_admin_revierte_una_entrega(self, sembrado: AsyncSession) -> None:
        """El permiso viaja en la fila, no en el código."""
        permiso = (
            await sembrado.execute(
                text("""
                    SELECT p.code FROM shipment_status_transitions t
                    JOIN permissions p ON p.id = t.required_permission_id
                    WHERE t.from_status_code = 'DELIVERED' AND t.to_status_code = 'DISPATCHED'
                """)
            )
        ).scalar_one()

        assert permiso == Perm.SHIPMENTS_TRANSITION_REVERT_DELIVERED

    async def test_cancelar_usa_permisos_distintos_segun_el_estado(
        self, sembrado: AsyncSession
    ) -> None:
        """ADR-0004: OPS_AGENT cancela desde PRE_ALERT, no desde IN_TRANSIT."""
        permisos = dict(
            (
                await sembrado.execute(
                    text("""
                        SELECT t.from_status_code, p.code
                        FROM shipment_status_transitions t
                        JOIN permissions p ON p.id = t.required_permission_id
                        WHERE t.to_status_code = 'CANCELLED'
                    """)
                )
            ).all()
        )

        assert permisos == {
            str(S.PRE_ALERT): Perm.SHIPMENTS_CANCEL_PREALERT,
            str(S.IN_TRANSIT): Perm.SHIPMENTS_CANCEL_IN_TRANSIT,
        }

    async def test_no_se_cancela_desde_recibida_en_adelante(self, sembrado: AsyncSession) -> None:
        """La carga ya existe físicamente: se cancela el despacho, no la carga."""
        origenes = set(
            (
                await sembrado.execute(
                    text("""
                        SELECT from_status_code FROM shipment_status_transitions
                        WHERE to_status_code = 'CANCELLED'
                    """)
                )
            )
            .scalars()
            .all()
        )

        assert origenes == {str(S.PRE_ALERT), str(S.IN_TRANSIT)}

    async def test_reabrir_vuelve_al_estado_del_que_se_cancelo(
        self, sembrado: AsyncSession
    ) -> None:
        destinos = set(
            (
                await sembrado.execute(
                    text("""
                        SELECT to_status_code FROM shipment_status_transitions
                        WHERE from_status_code = 'CANCELLED'
                    """)
                )
            )
            .scalars()
            .all()
        )

        assert destinos == {str(S.PRE_ALERT), str(S.IN_TRANSIT)}

    async def test_los_retrocesos_exigen_motivo(self, sembrado: AsyncSession) -> None:
        sin_motivo = (
            await sembrado.execute(
                text("""
                    SELECT count(*) FROM shipment_status_transitions t
                    JOIN permissions p ON p.id = t.required_permission_id
                    WHERE p.code IN (
                        'shipments.transition.backward',
                        'shipments.transition.revert_delivered',
                        'shipments.cancel.prealert',
                        'shipments.cancel.in_transit',
                        'shipments.reopen'
                    ) AND t.requires_reason = false
                """)
            )
        ).scalar_one()

        assert sin_motivo == 0

    async def test_avanzar_no_exige_motivo(self, sembrado: AsyncSession) -> None:
        con_motivo = (
            await sembrado.execute(
                text("""
                    SELECT count(*) FROM shipment_status_transitions t
                    JOIN permissions p ON p.id = t.required_permission_id
                    WHERE p.code = 'shipments.transition.forward' AND t.requires_reason = true
                """)
            )
        ).scalar_one()

        assert con_motivo == 0

    async def test_los_estados_finales_estan_marcados(self, sembrado: AsyncSession) -> None:
        terminales = set(
            (await sembrado.execute(text("SELECT code FROM shipment_statuses WHERE is_terminal")))
            .scalars()
            .all()
        )

        assert terminales == {str(S.DELIVERED), str(S.CANCELLED)}

    async def test_no_se_puede_borrar_un_estado_en_uso(self, sembrado: AsyncSession) -> None:
        """RESTRICT: retirar un estado con transiciones dejaría FKs rotos."""
        with pytest.raises(IntegrityError):
            await sembrado.execute(text("DELETE FROM shipment_statuses WHERE code = 'STORED'"))

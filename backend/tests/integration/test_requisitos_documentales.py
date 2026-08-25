"""Requisitos documentales enlazados (Paso 3.4).

Un documento obligatorio del catálogo bloquea el avance hasta que Operaciones
lo verifica o lo exonera. Subirlo NO alcanza (ADR-0003).
"""

import uuid

import pytest
from scripts.seed_document_types import sembrar as sembrar_documentos
from scripts.seed_rbac import sembrar as sembrar_rbac
from scripts.seed_shipment_statuses import sembrar as sembrar_estados
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.dispatches import service as despachos
from app.modules.dispatches.models import DispatchMethod
from app.modules.rbac.models import RoleCode, ScopeType
from app.modules.rbac.service import obtener_permisos_efectivos
from app.modules.shipments import service as cargas
from app.modules.shipments.models import RequirementStatus, ShipmentStatus

pytestmark = pytest.mark.integration


async def _entorno(session: AsyncSession) -> dict:
    await sembrar_rbac(session)
    await sembrar_estados(session)
    await sembrar_documentos(session)

    empresa = (
        await session.execute(
            text("INSERT INTO companies (legal_name, status) VALUES (:n,'ACTIVE') RETURNING id"),
            {"n": f"Req {uuid.uuid4().hex[:6]} S.A."},
        )
    ).scalar_one()
    ajena = (
        await session.execute(
            text("INSERT INTO companies (legal_name, status) VALUES (:n,'ACTIVE') RETURNING id"),
            {"n": f"Ajena {uuid.uuid4().hex[:6]} S.A."},
        )
    ).scalar_one()

    ctx: dict = {"empresa": empresa, "ajena": ajena}
    for clave, rol, alcance, scope_company in (
        ("admin", RoleCode.OPS_ADMIN, ScopeType.GLOBAL, None),
        ("agente", RoleCode.OPS_AGENT, ScopeType.GLOBAL, None),
        ("cliente", RoleCode.CLIENT_ADMIN, ScopeType.ORGANIZATION, empresa),
        ("cliente_ajeno", RoleCode.CLIENT_ADMIN, ScopeType.ORGANIZATION, ajena),
    ):
        ctx[clave] = await _usuario(session, rol, alcance, scope_company)

    ctx["origen"] = await _ubicacion(session, "US", "MIA")
    ctx["destino"] = await _ubicacion(session, "CR", "SJO")
    return ctx


async def _usuario(session: AsyncSession, rol: str, scope: str, empresa) -> uuid.UUID:
    user_id = (
        await session.execute(
            text("""
                INSERT INTO users (email, password_hash, first_name, last_name, status)
                VALUES (:e,'h','N','A','ACTIVE') RETURNING id
            """),
            {"e": f"r-{uuid.uuid4().hex[:10]}@amvarmar.com"},
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


async def _ubicacion(session: AsyncSession, pais: str, ciudad: str) -> uuid.UUID:
    return (
        await session.execute(
            text("""
                INSERT INTO locations (country_code, city_code, location_code, name)
                VALUES (:p,:c,:cod,:n)
                ON CONFLICT (location_code) DO UPDATE SET name = EXCLUDED.name
                RETURNING id
            """),
            {"p": pais, "c": ciudad, "cod": f"{pais}-{ciudad}", "n": ciudad},
        )
    ).scalar_one()


async def _bodega(session: AsyncSession, ctx: dict, *, usa_wr: bool) -> uuid.UUID:
    return (
        await session.execute(
            text("""
                INSERT INTO facilities
                    (location_id, facility_code, facility_type, uses_warehouse_receipt)
                VALUES (:l, :cod, 'WAREHOUSE', :wr) RETURNING id
            """),
            {
                "l": ctx["origen"],
                "cod": f"BOD-{uuid.uuid4().hex[:6]}",
                "wr": usa_wr,
            },
        )
    ).scalar_one()


async def _carga(
    session: AsyncSession,
    ctx: dict,
    *,
    estado: str = ShipmentStatus.STORED,
    bodega: uuid.UUID | None = None,
    permiso_especial: bool = False,
    empresa: str = "empresa",
) -> uuid.UUID:
    return (
        await session.execute(
            text("""
                INSERT INTO shipments
                    (company_id, created_by, current_status_code, origin_location_id,
                     destination_location_id, origin_facility_id, permit_review_required)
                VALUES (:c,:u,:estado,:o,:d,:f,:permiso) RETURNING id
            """),
            {
                "c": ctx[empresa],
                "u": ctx["admin"],
                "estado": estado,
                "o": ctx["origen"],
                "d": ctx["destino"],
                "f": bodega,
                "permiso": permiso_especial,
            },
        )
    ).scalar_one()


async def _permisos(session: AsyncSession, redis, user_id: uuid.UUID):
    return await obtener_permisos_efectivos(session, redis, user_id)


async def _requisitos(session: AsyncSession, shipment_id: uuid.UUID) -> dict[str, str]:
    filas = (
        await session.execute(
            text("""
                SELECT dt.code, r.status
                FROM shipment_requirements r
                JOIN document_types dt ON dt.id = r.document_type_id
                WHERE r.shipment_id = :s
            """),
            {"s": shipment_id},
        )
    ).all()
    return {f.code: f.status for f in filas}


async def _tipo(session: AsyncSession, code: str) -> uuid.UUID:
    return (
        await session.execute(text("SELECT id FROM document_types WHERE code = :c"), {"c": code})
    ).scalar_one()


async def _requisito_de(session: AsyncSession, shipment_id: uuid.UUID, code: str) -> uuid.UUID:
    return (
        await session.execute(
            text("""
                SELECT r.id FROM shipment_requirements r
                JOIN document_types dt ON dt.id = r.document_type_id
                WHERE r.shipment_id = :s AND dt.code = :c
            """),
            {"s": shipment_id, "c": code},
        )
    ).scalar_one()


class TestAperturaDesdeElCatalogo:
    """`required_before_status` deja de ser configuración muerta."""

    async def test_recibir_abre_los_requisitos_obligatorios(
        self, session: AsyncSession, redis
    ) -> None:
        ctx = await _entorno(session)
        carga = await _carga(
            session,
            ctx,
            estado=ShipmentStatus.IN_TRANSIT,
            bodega=await _bodega(session, ctx, usa_wr=False),
        )

        await cargas.transicionar(
            session,
            shipment_id=carga,
            datos=cargas.DatosTransicion(to_status=ShipmentStatus.RECEIVED, row_version=1),
            actor_user_id=ctx["admin"],
            permisos=await _permisos(session, redis, ctx["admin"]),
        )

        abiertos = await _requisitos(session, carga)
        assert abiertos["COMMERCIAL_INVOICE"] == RequirementStatus.PENDING
        assert abiertos["PACKING_LIST"] == RequirementStatus.PENDING
        assert abiertos["PROOF_OF_DELIVERY"] == RequirementStatus.PENDING
        # El BL se carga después del despacho: nunca es un pendiente (ADR-0006).
        assert "BL" not in abiertos

    async def test_la_sli_depende_de_la_bodega_no_de_la_ciudad(
        self, session: AsyncSession, redis
    ) -> None:
        """ADR-0005: la regla se apoya en el flag, no en comparar Miami."""
        ctx = await _entorno(session)
        sin_wr = await _carga(
            session,
            ctx,
            estado=ShipmentStatus.IN_TRANSIT,
            bodega=await _bodega(session, ctx, usa_wr=False),
        )
        con_wr = await _carga(
            session,
            ctx,
            estado=ShipmentStatus.IN_TRANSIT,
            bodega=await _bodega(session, ctx, usa_wr=True),
        )

        for carga in (sin_wr, con_wr):
            await cargas.transicionar(
                session,
                shipment_id=carga,
                datos=cargas.DatosTransicion(to_status=ShipmentStatus.RECEIVED, row_version=1),
                actor_user_id=ctx["admin"],
                permisos=await _permisos(session, redis, ctx["admin"]),
            )

        # Las dos salen de la MISMA ubicación: solo cambia el flag de la bodega.
        assert "SLI" not in await _requisitos(session, sin_wr)
        assert "SLI" in await _requisitos(session, con_wr)

    async def test_el_permiso_especial_solo_si_operaciones_lo_marca(
        self, session: AsyncSession, redis
    ) -> None:
        ctx = await _entorno(session)
        normal = await _carga(session, ctx, estado=ShipmentStatus.IN_TRANSIT)
        marcada = await _carga(
            session, ctx, estado=ShipmentStatus.IN_TRANSIT, permiso_especial=True
        )

        for carga in (normal, marcada):
            await cargas.transicionar(
                session,
                shipment_id=carga,
                datos=cargas.DatosTransicion(to_status=ShipmentStatus.RECEIVED, row_version=1),
                actor_user_id=ctx["admin"],
                permisos=await _permisos(session, redis, ctx["admin"]),
            )

        assert "SPECIAL_PERMIT" not in await _requisitos(session, normal)
        assert "SPECIAL_PERMIT" in await _requisitos(session, marcada)

    async def test_es_idempotente_y_no_revive_lo_ya_resuelto(
        self, session: AsyncSession, redis
    ) -> None:
        """Se llama al recibir y al almacenar: la segunda vez no debe duplicar.

        Y sobre todo, no debe reabrir lo que Operaciones ya exoneró: eso
        borraría la decisión sin dejar rastro.
        """
        ctx = await _entorno(session)
        carga = await _carga(session, ctx, estado=ShipmentStatus.RECEIVED)
        await cargas.sincronizar_requisitos_del_catalogo(
            session, shipment_id=carga, actor_user_id=ctx["admin"]
        )
        primera = await _requisitos(session, carga)

        await cargas.resolver_requisito(
            session,
            requirement_id=await _requisito_de(session, carga, "COMMERCIAL_INVOICE"),
            nuevo_estado=RequirementStatus.WAIVED,
            actor_user_id=ctx["admin"],
            permisos=await _permisos(session, redis, ctx["admin"]),
            motivo="La factura llegó por otra vía.",
        )

        abiertos = await cargas.sincronizar_requisitos_del_catalogo(
            session, shipment_id=carga, actor_user_id=ctx["admin"]
        )

        assert abiertos == []
        segunda = await _requisitos(session, carga)
        assert set(segunda) == set(primera)
        assert segunda["COMMERCIAL_INVOICE"] == RequirementStatus.WAIVED


class TestBloqueoDelDespacho:
    """El gate del paso: no se aprueba un despacho sin los documentos."""

    async def _carga_con_pendiente(self, session: AsyncSession, ctx: dict) -> uuid.UUID:
        carga = await _carga(session, ctx)
        await cargas.sincronizar_requisitos_del_catalogo(
            session, shipment_id=carga, actor_user_id=ctx["admin"]
        )
        return carga

    async def test_aprobar_con_requisito_abierto_da_conflicto(
        self, session: AsyncSession, redis
    ) -> None:
        ctx = await _entorno(session)
        carga = await self._carga_con_pendiente(session, ctx)
        solicitud = await despachos.crear(
            session,
            company_id=ctx["empresa"],
            actor_user_id=ctx["admin"],
            method=DispatchMethod.SEA.value,
            shipment_ids=[carga],
            permisos=await _permisos(session, redis, ctx["admin"]),
        )

        with pytest.raises(cargas.RequisitosPendientes) as error:
            await despachos.aprobar(
                session,
                dispatch_id=solicitud.id,
                actor_user_id=ctx["admin"],
                permisos=await _permisos(session, redis, ctx["admin"]),
                company_ids=None,
            )

        assert error.value.code == "SHIPMENT_REQUIREMENTS_PENDING"
        titulos = {d["titulo"] for d in error.value.details}
        assert "Factura comercial" in titulos

    async def test_el_detalle_dice_de_que_carga_falta_cada_cosa(
        self, session: AsyncSession, redis
    ) -> None:
        """Con varias cargas, "falta un documento" no basta para actuar."""
        ctx = await _entorno(session)
        una = await self._carga_con_pendiente(session, ctx)
        otra = await self._carga_con_pendiente(session, ctx)
        solicitud = await despachos.crear(
            session,
            company_id=ctx["empresa"],
            actor_user_id=ctx["admin"],
            method=DispatchMethod.SEA.value,
            shipment_ids=[una, otra],
            permisos=await _permisos(session, redis, ctx["admin"]),
        )

        with pytest.raises(cargas.RequisitosPendientes) as error:
            await despachos.aprobar(
                session,
                dispatch_id=solicitud.id,
                actor_user_id=ctx["admin"],
                permisos=await _permisos(session, redis, ctx["admin"]),
                company_ids=None,
            )

        numeros = {d["carga"] for d in error.value.details}
        assert len(numeros) == 2

    async def test_la_prueba_de_entrega_no_impide_despachar(
        self, session: AsyncSession, redis
    ) -> None:
        """Bloquea DELIVERED, no DISPATCHED: exigirla antes sería imposible."""
        ctx = await _entorno(session)
        carga = await self._carga_con_pendiente(session, ctx)

        for code in ("COMMERCIAL_INVOICE", "PACKING_LIST"):
            await cargas.resolver_requisito(
                session,
                requirement_id=await _requisito_de(session, carga, code),
                nuevo_estado=RequirementStatus.VERIFIED,
                actor_user_id=ctx["admin"],
                permisos=await _permisos(session, redis, ctx["admin"]),
            )

        solicitud = await despachos.crear(
            session,
            company_id=ctx["empresa"],
            actor_user_id=ctx["admin"],
            method=DispatchMethod.SEA.value,
            shipment_ids=[carga],
            permisos=await _permisos(session, redis, ctx["admin"]),
        )

        # La prueba de entrega sigue PENDING y aun así el despacho avanza.
        aprobada = await despachos.aprobar(
            session,
            dispatch_id=solicitud.id,
            actor_user_id=ctx["admin"],
            permisos=await _permisos(session, redis, ctx["admin"]),
            company_ids=None,
        )
        assert aprobada.hacia == "APPROVED"
        assert (await _requisitos(session, carga))["PROOF_OF_DELIVERY"] == (
            RequirementStatus.PENDING
        )


class TestExoneracion:
    async def test_exonerar_desbloquea_y_deja_el_motivo(self, session: AsyncSession, redis) -> None:
        ctx = await _entorno(session)
        carga = await _carga(session, ctx)
        await cargas.sincronizar_requisitos_del_catalogo(
            session, shipment_id=carga, actor_user_id=ctx["admin"]
        )

        for code in ("COMMERCIAL_INVOICE", "PACKING_LIST"):
            await cargas.resolver_requisito(
                session,
                requirement_id=await _requisito_de(session, carga, code),
                nuevo_estado=RequirementStatus.WAIVED,
                actor_user_id=ctx["admin"],
                permisos=await _permisos(session, redis, ctx["admin"]),
                motivo="Documentación consolidada en el expediente del contenedor.",
            )

        solicitud = await despachos.crear(
            session,
            company_id=ctx["empresa"],
            actor_user_id=ctx["admin"],
            method=DispatchMethod.SEA.value,
            shipment_ids=[carga],
            permisos=await _permisos(session, redis, ctx["admin"]),
        )
        await despachos.aprobar(
            session,
            dispatch_id=solicitud.id,
            actor_user_id=ctx["admin"],
            permisos=await _permisos(session, redis, ctx["admin"]),
            company_ids=None,
        )

        motivo = (
            await session.execute(
                text("""
                    SELECT resolution_reason FROM shipment_requirements r
                    JOIN document_types dt ON dt.id = r.document_type_id
                    WHERE r.shipment_id = :s AND dt.code = 'COMMERCIAL_INVOICE'
                """),
                {"s": carga},
            )
        ).scalar_one()
        assert "consolidada" in motivo

    async def test_un_agente_no_puede_exonerar(self, session: AsyncSession, redis) -> None:
        """Exonerar deja avanzar SIN el documento: es de OPS_ADMIN para arriba."""
        ctx = await _entorno(session)
        carga = await _carga(session, ctx)
        await cargas.sincronizar_requisitos_del_catalogo(
            session, shipment_id=carga, actor_user_id=ctx["admin"]
        )

        with pytest.raises(cargas.SinPermisoSobreRequisito):
            await cargas.resolver_requisito(
                session,
                requirement_id=await _requisito_de(session, carga, "COMMERCIAL_INVOICE"),
                nuevo_estado=RequirementStatus.WAIVED,
                actor_user_id=ctx["agente"],
                permisos=await _permisos(session, redis, ctx["agente"]),
                motivo="Me parece que no hace falta.",
            )

    async def test_un_agente_si_puede_verificar(self, session: AsyncSession, redis) -> None:
        """El corte es exonerar, no resolver: verificar sigue siendo su trabajo."""
        ctx = await _entorno(session)
        carga = await _carga(session, ctx)
        await cargas.sincronizar_requisitos_del_catalogo(
            session, shipment_id=carga, actor_user_id=ctx["admin"]
        )

        await cargas.resolver_requisito(
            session,
            requirement_id=await _requisito_de(session, carga, "COMMERCIAL_INVOICE"),
            nuevo_estado=RequirementStatus.VERIFIED,
            actor_user_id=ctx["agente"],
            permisos=await _permisos(session, redis, ctx["agente"]),
        )

        assert (await _requisitos(session, carga))["COMMERCIAL_INVOICE"] == (
            RequirementStatus.VERIFIED
        )


class TestAlcance:
    """Antes del Paso 3.4 estos endpoints solo exigían estar autenticado."""

    async def test_un_cliente_no_exonera_sus_propios_requisitos(
        self, session: AsyncSession, redis
    ) -> None:
        ctx = await _entorno(session)
        carga = await _carga(session, ctx)
        await cargas.sincronizar_requisitos_del_catalogo(
            session, shipment_id=carga, actor_user_id=ctx["admin"]
        )

        with pytest.raises(cargas.SinPermisoSobreRequisito):
            await cargas.resolver_requisito(
                session,
                requirement_id=await _requisito_de(session, carga, "COMMERCIAL_INVOICE"),
                nuevo_estado=RequirementStatus.WAIVED,
                actor_user_id=ctx["cliente"],
                permisos=await _permisos(session, redis, ctx["cliente"]),
                motivo="No la tengo.",
            )

    async def test_nadie_toca_los_requisitos_de_otra_empresa(
        self, session: AsyncSession, redis
    ) -> None:
        ctx = await _entorno(session)
        carga = await _carga(session, ctx)

        with pytest.raises(cargas.SinPermisoSobreRequisito):
            await cargas.abrir_requisito(
                session,
                shipment_id=carga,
                requirement_type="INFORMATION",
                title="Requisito inventado",
                required_from="CLIENT",
                actor_user_id=ctx["cliente_ajeno"],
                permisos=await _permisos(session, redis, ctx["cliente_ajeno"]),
            )

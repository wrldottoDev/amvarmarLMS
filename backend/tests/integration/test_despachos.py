"""Solicitudes de despacho (Paso 3.3 + ADR-0013)."""

import asyncio
import uuid

import pytest
from scripts.seed_document_types import sembrar as sembrar_documentos
from scripts.seed_rbac import sembrar as sembrar_rbac
from scripts.seed_shipment_statuses import sembrar as sembrar_estados
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.errors import RecursoNoEncontrado
from app.modules.dispatches import service
from app.modules.dispatches.models import DispatchMethod, DispatchStatus
from app.modules.rbac.models import RoleCode, ScopeType
from app.modules.rbac.service import obtener_permisos_efectivos
from app.modules.shipments.models import ShipmentStatus

pytestmark = pytest.mark.integration


async def _entorno(session: AsyncSession) -> dict[str, object]:
    await sembrar_rbac(session)
    await sembrar_estados(session)
    await sembrar_documentos(session)

    empresa = await _empresa(session, f"Desp {uuid.uuid4().hex[:6]} S.A.")
    otra = await _empresa(session, f"Otra {uuid.uuid4().hex[:6]} S.A.")

    operaciones = await _usuario_con_rol(session, RoleCode.OPS_ADMIN, ScopeType.GLOBAL, None)
    agente = await _usuario_con_rol(session, RoleCode.OPS_AGENT, ScopeType.GLOBAL, None)
    cliente = await _usuario_con_rol(
        session, RoleCode.CLIENT_ADMIN, ScopeType.ORGANIZATION, empresa
    )

    origen = await _ubicacion(session, "US", "MIA", "Miami")
    destino = await _ubicacion(session, "CR", "SJO", "San José")

    return {
        "empresa": empresa,
        "otra_empresa": otra,
        "operaciones": operaciones,
        "agente": agente,
        "cliente": cliente,
        "origen": origen,
        "destino": destino,
    }


async def _empresa(session: AsyncSession, nombre: str) -> uuid.UUID:
    return (
        await session.execute(
            text("INSERT INTO companies (legal_name, status) VALUES (:n,'ACTIVE') RETURNING id"),
            {"n": nombre},
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
            {"e": f"d-{uuid.uuid4().hex[:10]}@amvarmar.com"},
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


async def _carga_almacenada(
    session: AsyncSession, ctx: dict, *, empresa: str = "empresa"
) -> uuid.UUID:
    return (
        await session.execute(
            text("""
                INSERT INTO shipments
                    (company_id, created_by, current_status_code,
                     origin_location_id, destination_location_id)
                VALUES (:c,:u,'STORED',:o,:d) RETURNING id
            """),
            {
                "c": ctx[empresa],
                "u": ctx["operaciones"],
                "o": ctx["origen"],
                "d": ctx["destino"],
            },
        )
    ).scalar_one()


async def _permisos(session: AsyncSession, redis, user_id: uuid.UUID):
    return await obtener_permisos_efectivos(session, redis, user_id)


async def _crear(
    session: AsyncSession, redis, ctx: dict, cargas: list[uuid.UUID], *, actor: str = "operaciones"
):
    return await service.crear(
        session,
        company_id=ctx["empresa"],
        actor_user_id=ctx[actor],
        method=DispatchMethod.SEA.value,
        shipment_ids=cargas,
        permisos=await _permisos(session, redis, ctx[actor]),
    )


async def _estado_carga(session: AsyncSession, shipment_id: uuid.UUID) -> str:
    return (
        await session.execute(
            text("SELECT current_status_code FROM shipments WHERE id = :id"), {"id": shipment_id}
        )
    ).scalar_one()


class TestMetodosDeTransporte:
    """Solo `SEA`, `AIR` y `LAND`.

    `PICKUP` nunca describió un modo de transporte sino quién retiraba la
    mercancía, que es otra dimensión. Mezclarlos hacía imposible saber si un
    despacho salió por mar o por aire.
    """

    def test_el_catalogo_tiene_exactamente_tres_metodos(self) -> None:
        assert {m.value for m in DispatchMethod} == {"SEA", "AIR", "LAND"}

    async def test_la_base_rechaza_un_metodo_fuera_del_catalogo(
        self, session: AsyncSession
    ) -> None:
        """El CHECK es la última defensa.

        Si mañana alguien reintroduce el valor en un enum de Python, o escribe
        directo contra la base, la restricción lo frena igual.
        """
        ctx = await _entorno(session)

        with pytest.raises(IntegrityError):
            await session.execute(
                text("""
                    INSERT INTO dispatch_requests (company_id, requested_by, method, status)
                    VALUES (:c, :u, 'PICKUP', 'PENDING')
                """),
                {"c": ctx["empresa"], "u": ctx["cliente"]},
            )


class TestCreacion:
    async def test_reclama_las_cargas_y_las_mueve(self, session: AsyncSession, redis) -> None:
        ctx = await _entorno(session)
        cargas = [await _carga_almacenada(session, ctx) for _ in range(2)]

        solicitud = await _crear(session, redis, ctx, cargas)

        assert solicitud.status == DispatchStatus.PENDING
        assert solicitud.dispatch_number.startswith("DSP-")
        for carga in cargas:
            assert await _estado_carga(session, carga) == ShipmentStatus.DISPATCH_REQUESTED

    async def test_deja_evento_en_la_solicitud(self, session: AsyncSession, redis) -> None:
        ctx = await _entorno(session)
        solicitud = await _crear(session, redis, ctx, [await _carga_almacenada(session, ctx)])

        eventos = (
            await session.execute(
                text(
                    "SELECT event_type, to_status FROM dispatch_events WHERE dispatch_request_id = :d"
                ),
                {"d": solicitud.id},
            )
        ).all()

        assert len(eventos) == 1
        assert eventos[0].to_status == DispatchStatus.PENDING

    async def test_una_carga_no_almacenada_se_rechaza(self, session: AsyncSession, redis) -> None:
        """Solo se despacha lo que está en bodega."""
        ctx = await _entorno(session)
        carga = await _carga_almacenada(session, ctx)
        await session.execute(
            text("UPDATE shipments SET current_status_code = 'IN_TRANSIT' WHERE id = :id"),
            {"id": carga},
        )

        with pytest.raises(service.TransicionDeDespachoInvalida) as error:
            await _crear(session, redis, ctx, [carga])

        assert error.value.details[0]["status"] == "IN_TRANSIT"

    async def test_una_carga_de_otra_empresa_da_404(self, session: AsyncSession, redis) -> None:
        """Mezclar empresas expondría datos de una a la otra."""
        ctx = await _entorno(session)
        ajena = await _carga_almacenada(session, ctx, empresa="otra_empresa")

        with pytest.raises(RecursoNoEncontrado):
            await _crear(session, redis, ctx, [ajena])

    async def test_sin_cargas_se_rechaza(self, session: AsyncSession, redis) -> None:
        ctx = await _entorno(session)

        with pytest.raises(service.DespachoSinCargas):
            await _crear(session, redis, ctx, [])

    async def test_una_carga_ya_reclamada_se_rechaza(self, session: AsyncSession, redis) -> None:
        ctx = await _entorno(session)
        carga = await _carga_almacenada(session, ctx)
        await _crear(session, redis, ctx, [carga])

        # La segunda solicitud ni siquiera pasa el filtro de estado, porque la
        # carga ya está en DISPATCH_REQUESTED.
        with pytest.raises(service.TransicionDeDespachoInvalida):
            await _crear(session, redis, ctx, [carga])


class TestFlujoCompleto:
    async def test_aprobar_preparar_completar(self, session: AsyncSession, redis) -> None:
        ctx = await _entorno(session)
        carga = await _carga_almacenada(session, ctx)
        solicitud = await _crear(session, redis, ctx, [carga])
        permisos = await _permisos(session, redis, ctx["operaciones"])
        comun = {
            "dispatch_id": solicitud.id,
            "actor_user_id": ctx["operaciones"],
            "permisos": permisos,
            "company_ids": None,
        }

        await service.aprobar(session, **comun)
        assert await _estado_carga(session, carga) == ShipmentStatus.DISPATCH_REQUESTED

        await service.preparar(session, **comun)
        assert await _estado_carga(session, carga) == ShipmentStatus.PREPARING

        resultado = await service.completar(session, **comun)

        assert resultado.hacia == DispatchStatus.COMPLETED
        assert await _estado_carga(session, carga) == ShipmentStatus.DISPATCHED

    async def test_completar_libera_las_cargas(self, session: AsyncSession, redis) -> None:
        """El despacho terminó: ya no las retiene."""
        ctx = await _entorno(session)
        carga = await _carga_almacenada(session, ctx)
        solicitud = await _crear(session, redis, ctx, [carga])
        permisos = await _permisos(session, redis, ctx["operaciones"])
        comun = {
            "dispatch_id": solicitud.id,
            "actor_user_id": ctx["operaciones"],
            "permisos": permisos,
            "company_ids": None,
        }
        await service.aprobar(session, **comun)
        await service.preparar(session, **comun)
        await service.completar(session, **comun)

        liberada = (
            await session.execute(
                text("""
                    SELECT released_at FROM dispatch_request_shipments
                    WHERE dispatch_request_id = :d
                """),
                {"d": solicitud.id},
            )
        ).scalar_one()
        assert liberada is not None

    async def test_una_transicion_invalida_da_conflicto(self, session: AsyncSession, redis) -> None:
        """No se completa una solicitud que nadie aprobó."""
        ctx = await _entorno(session)
        solicitud = await _crear(session, redis, ctx, [await _carga_almacenada(session, ctx)])

        with pytest.raises(service.TransicionDeDespachoInvalida) as error:
            await service.completar(
                session,
                dispatch_id=solicitud.id,
                actor_user_id=ctx["operaciones"],
                permisos=await _permisos(session, redis, ctx["operaciones"]),
                company_ids=None,
            )

        assert error.value.status_code == 409
        assert error.value.details[0]["from"] == DispatchStatus.PENDING

    async def test_cada_accion_deja_su_evento(self, session: AsyncSession, redis) -> None:
        ctx = await _entorno(session)
        solicitud = await _crear(session, redis, ctx, [await _carga_almacenada(session, ctx)])
        permisos = await _permisos(session, redis, ctx["operaciones"])
        comun = {
            "dispatch_id": solicitud.id,
            "actor_user_id": ctx["operaciones"],
            "permisos": permisos,
            "company_ids": None,
        }
        await service.aprobar(session, **comun)
        await service.preparar(session, **comun)
        await service.completar(session, **comun)

        tipos = list(
            (
                await session.execute(
                    text("""
                        SELECT event_type FROM dispatch_events
                        WHERE dispatch_request_id = :d ORDER BY occurred_at, id
                    """),
                    {"d": solicitud.id},
                )
            )
            .scalars()
            .all()
        )

        assert tipos == ["CREATED", "APPROVED", "PREPARING", "COMPLETED"]


class TestRechazo:
    async def test_sin_motivo_se_rechaza(self, session: AsyncSession, redis) -> None:
        ctx = await _entorno(session)
        solicitud = await _crear(session, redis, ctx, [await _carga_almacenada(session, ctx)])

        with pytest.raises(service.MotivoRequerido):
            await service.rechazar(
                session,
                dispatch_id=solicitud.id,
                actor_user_id=ctx["operaciones"],
                permisos=await _permisos(session, redis, ctx["operaciones"]),
                company_ids=None,
                motivo="   ",
            )

    async def test_devuelve_las_cargas_a_almacenada(self, session: AsyncSession, redis) -> None:
        """ADR-0013: rechazar autoriza devolver las cargas."""
        ctx = await _entorno(session)
        carga = await _carga_almacenada(session, ctx)
        solicitud = await _crear(session, redis, ctx, [carga])

        await service.rechazar(
            session,
            dispatch_id=solicitud.id,
            actor_user_id=ctx["operaciones"],
            permisos=await _permisos(session, redis, ctx["operaciones"]),
            company_ids=None,
            motivo="Falta la factura comercial.",
        )

        assert await _estado_carga(session, carga) == ShipmentStatus.STORED

    async def test_un_agente_puede_rechazar_pese_a_no_retroceder_estados(
        self, session: AsyncSession, redis
    ) -> None:
        """El caso que motivó ADR-0013.

        `OPS_AGENT` no tiene `shipments.transition.backward`, pero rechazar un
        despacho autoriza devolver sus cargas.
        """
        ctx = await _entorno(session)
        carga = await _carga_almacenada(session, ctx)
        solicitud = await _crear(session, redis, ctx, [carga])
        permisos_agente = await _permisos(session, redis, ctx["agente"])

        from app.modules.rbac.catalog import Perm

        assert Perm.SHIPMENTS_TRANSITION_BACKWARD not in permisos_agente.codigos()

        await service.rechazar(
            session,
            dispatch_id=solicitud.id,
            actor_user_id=ctx["agente"],
            permisos=permisos_agente,
            company_ids=None,
            motivo="Documentos incompletos.",
        )

        assert await _estado_carga(session, carga) == ShipmentStatus.STORED

    async def test_la_carga_devuelta_puede_entrar_en_otra_solicitud(
        self, session: AsyncSession, redis
    ) -> None:
        """Es el motivo de devolverlas: que no queden atrapadas."""
        ctx = await _entorno(session)
        carga = await _carga_almacenada(session, ctx)
        primera = await _crear(session, redis, ctx, [carga])
        await service.rechazar(
            session,
            dispatch_id=primera.id,
            actor_user_id=ctx["operaciones"],
            permisos=await _permisos(session, redis, ctx["operaciones"]),
            company_ids=None,
            motivo="Corregir dirección.",
        )

        segunda = await _crear(session, redis, ctx, [carga])

        assert segunda.id != primera.id
        assert await _estado_carga(session, carga) == ShipmentStatus.DISPATCH_REQUESTED

    async def test_la_devolucion_deja_el_motivo_en_la_carga(
        self, session: AsyncSession, redis
    ) -> None:
        """En la línea de tiempo de la carga se ve la causa, no un retroceso
        sin explicación."""
        ctx = await _entorno(session)
        carga = await _carga_almacenada(session, ctx)
        solicitud = await _crear(session, redis, ctx, [carga])

        await service.rechazar(
            session,
            dispatch_id=solicitud.id,
            actor_user_id=ctx["operaciones"],
            permisos=await _permisos(session, redis, ctx["operaciones"]),
            company_ids=None,
            motivo="Falta la factura.",
        )

        nota = (
            await session.execute(
                text("""
                    SELECT description FROM shipment_events
                    WHERE shipment_id = :s AND to_status_code = 'STORED'
                    ORDER BY occurred_at DESC LIMIT 1
                """),
                {"s": carga},
            )
        ).scalar_one()
        assert solicitud.dispatch_number in nota
        assert "Falta la factura" in nota


class TestCancelacion:
    """ADR-0013: el cliente cancela solo antes de la aprobación."""

    async def test_el_cliente_cancela_una_solicitud_pendiente(
        self, session: AsyncSession, redis
    ) -> None:
        ctx = await _entorno(session)
        carga = await _carga_almacenada(session, ctx)
        solicitud = await _crear(session, redis, ctx, [carga], actor="cliente")

        resultado = await service.cancelar(
            session,
            dispatch_id=solicitud.id,
            actor_user_id=ctx["cliente"],
            permisos=await _permisos(session, redis, ctx["cliente"]),
            company_ids=[ctx["empresa"]],
            motivo="Ya no lo necesito.",
        )

        assert resultado.hacia == DispatchStatus.CANCELLED
        assert await _estado_carga(session, carga) == ShipmentStatus.STORED

    async def test_el_cliente_no_cancela_una_solicitud_aprobada(
        self, session: AsyncSession, redis
    ) -> None:
        """Puede haber contenedor reservado o transporte contratado."""
        ctx = await _entorno(session)
        solicitud = await _crear(
            session, redis, ctx, [await _carga_almacenada(session, ctx)], actor="cliente"
        )
        await service.aprobar(
            session,
            dispatch_id=solicitud.id,
            actor_user_id=ctx["operaciones"],
            permisos=await _permisos(session, redis, ctx["operaciones"]),
            company_ids=None,
        )

        with pytest.raises(service.CancelacionNoPermitida) as error:
            await service.cancelar(
                session,
                dispatch_id=solicitud.id,
                actor_user_id=ctx["cliente"],
                permisos=await _permisos(session, redis, ctx["cliente"]),
                company_ids=[ctx["empresa"]],
            )

        assert error.value.code == "DISPATCH_YA_APROBADO"

    async def test_operaciones_si_cancela_una_aprobada(self, session: AsyncSession, redis) -> None:
        ctx = await _entorno(session)
        carga = await _carga_almacenada(session, ctx)
        solicitud = await _crear(session, redis, ctx, [carga])
        permisos = await _permisos(session, redis, ctx["operaciones"])
        await service.aprobar(
            session,
            dispatch_id=solicitud.id,
            actor_user_id=ctx["operaciones"],
            permisos=permisos,
            company_ids=None,
        )

        resultado = await service.cancelar(
            session,
            dispatch_id=solicitud.id,
            actor_user_id=ctx["operaciones"],
            permisos=permisos,
            company_ids=None,
            motivo="El cliente lo pidió por teléfono.",
        )

        assert resultado.hacia == DispatchStatus.CANCELLED
        assert await _estado_carga(session, carga) == ShipmentStatus.STORED

    async def test_no_se_cancela_una_solicitud_ya_completada(
        self, session: AsyncSession, redis
    ) -> None:
        ctx = await _entorno(session)
        solicitud = await _crear(session, redis, ctx, [await _carga_almacenada(session, ctx)])
        permisos = await _permisos(session, redis, ctx["operaciones"])
        comun = {
            "dispatch_id": solicitud.id,
            "actor_user_id": ctx["operaciones"],
            "permisos": permisos,
            "company_ids": None,
        }
        await service.aprobar(session, **comun)
        await service.preparar(session, **comun)
        await service.completar(session, **comun)

        with pytest.raises(service.TransicionDeDespachoInvalida):
            await service.cancelar(session, **comun)


class TestConcurrencia:
    """El gate del paso: dos solicitudes no pueden reclamar la misma carga."""

    @pytest.mark.parametrize("intento", range(20))
    async def test_doble_reclamo_simultaneo(
        self, migrated_database: str, redis, intento: int
    ) -> None:
        engine = create_async_engine(migrated_database)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        async with factory() as preparacion:
            ctx = await _entorno(preparacion)
            carga = await _carga_almacenada(preparacion, ctx)
            await preparacion.commit()

        async def solicitar() -> str:
            async with factory() as s:
                try:
                    await service.crear(
                        s,
                        company_id=ctx["empresa"],
                        actor_user_id=ctx["operaciones"],
                        method=DispatchMethod.SEA.value,
                        shipment_ids=[carga],
                        permisos=await obtener_permisos_efectivos(s, redis, ctx["operaciones"]),
                    )
                    await s.commit()
                    return "ok"
                except (
                    service.CargaNoDisponible,
                    service.TransicionDeDespachoInvalida,
                ):
                    await s.rollback()
                    return "rechazada"
                except Exception:
                    await s.rollback()
                    return "error"

        try:
            resultados = await asyncio.gather(solicitar(), solicitar())

            assert sorted(resultados) == ["ok", "rechazada"], (
                f"intento {intento}: se esperaba exactamente una ganadora, salió {resultados}"
            )

            # Y en la base quedó una sola relación activa.
            async with factory() as verificacion:
                activas = (
                    await verificacion.execute(
                        text("""
                            SELECT count(*) FROM dispatch_request_shipments
                            WHERE shipment_id = :s AND released_at IS NULL
                        """),
                        {"s": carga},
                    )
                ).scalar_one()
            assert activas == 1
        finally:
            async with factory() as limpieza:
                await limpieza.execute(
                    text("DELETE FROM dispatch_request_shipments WHERE shipment_id = :s"),
                    {"s": carga},
                )
                await limpieza.execute(
                    text(
                        "DELETE FROM dispatch_events WHERE dispatch_request_id IN "
                        "(SELECT id FROM dispatch_requests WHERE company_id = :c)"
                    ),
                    {"c": ctx["empresa"]},
                )
                await limpieza.execute(
                    text("DELETE FROM dispatch_requests WHERE company_id = :c"),
                    {"c": ctx["empresa"]},
                )
                # `shipment_events` es append-only por trigger (ADR-0003), así que
                # el borrado de limpieza lo desactiva a propósito y solo aquí.
                # No hay forma de limpiar por SQL normal, y la alternativa —
                # dejar las filas — haría que cada corrida arrastre la anterior.
                # La garantía de producción no se toca: el trigger vuelve a
                # quedar activo antes del commit.
                await limpieza.execute(
                    text(
                        "ALTER TABLE shipment_events DISABLE TRIGGER trg_shipment_events_inmutable"
                    )
                )
                await limpieza.execute(
                    text("DELETE FROM shipment_events WHERE shipment_id = :s"), {"s": carga}
                )
                await limpieza.execute(
                    text("ALTER TABLE shipment_events ENABLE TRIGGER trg_shipment_events_inmutable")
                )
                await limpieza.execute(text("DELETE FROM shipments WHERE id = :s"), {"s": carga})

                # Esta prueba committea de verdad — no puede usar la transacción
                # que se revierte —, así que también le toca borrar las empresas
                # y usuarios que sembró. Si no, cada corrida los va acumulando y
                # rompe pruebas ajenas que cuentan filas.
                empresas = [ctx["empresa"], ctx["otra_empresa"]]
                usuarios = [ctx["operaciones"], ctx["agente"], ctx["cliente"]]
                await limpieza.execute(
                    text("DELETE FROM user_role_assignments WHERE user_id = ANY(:u)"),
                    {"u": usuarios},
                )
                await limpieza.execute(
                    text("DELETE FROM company_memberships WHERE company_id = ANY(:c)"),
                    {"c": empresas},
                )
                await limpieza.execute(
                    text("DELETE FROM users WHERE id = ANY(:u)"), {"u": usuarios}
                )
                await limpieza.execute(
                    text("DELETE FROM companies WHERE id = ANY(:c)"), {"c": empresas}
                )
                await limpieza.commit()
            await engine.dispose()

    async def test_el_indice_unico_impide_el_doble_reclamo_por_sql_directo(
        self, session: AsyncSession, redis
    ) -> None:
        """La garantía real no es el FOR UPDATE: es el índice único parcial.

        Vale aunque alguien inserte sin pasar por el servicio.
        """
        from sqlalchemy.exc import IntegrityError

        ctx = await _entorno(session)
        carga = await _carga_almacenada(session, ctx)
        primera = await _crear(session, redis, ctx, [carga])

        otra = (
            await session.execute(
                text("""
                    INSERT INTO dispatch_requests
                        (company_id, requested_by, method, status)
                    VALUES (:c, :u, 'SEA', 'PENDING') RETURNING id
                """),
                {"c": ctx["empresa"], "u": ctx["operaciones"]},
            )
        ).scalar_one()

        with pytest.raises(IntegrityError):
            await session.execute(
                text("""
                    INSERT INTO dispatch_request_shipments (dispatch_request_id, shipment_id)
                    VALUES (:d, :s)
                """),
                {"d": otra, "s": carga},
            )

        assert primera.id is not None

    async def test_una_carga_liberada_si_admite_otra_solicitud(
        self, session: AsyncSession, redis
    ) -> None:
        """El índice es parcial: solo cuenta lo no liberado."""
        ctx = await _entorno(session)
        carga = await _carga_almacenada(session, ctx)
        primera = await _crear(session, redis, ctx, [carga])
        await service.cancelar(
            session,
            dispatch_id=primera.id,
            actor_user_id=ctx["operaciones"],
            permisos=await _permisos(session, redis, ctx["operaciones"]),
            company_ids=None,
            motivo="Se rehace.",
        )

        segunda = await _crear(session, redis, ctx, [carga])

        assert segunda.id != primera.id

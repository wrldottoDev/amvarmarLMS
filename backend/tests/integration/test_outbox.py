"""Transactional outbox (Paso 4.1).

Las cuatro pruebas que pide el plan, más las del backoff y el agotamiento.
La propiedad central: un evento no se pierde ni se entrega dos veces, aunque
el worker muera a mitad.
"""

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.modules.audit import outbox
from app.modules.shipments import service as cargas
from app.modules.shipments.models import ShipmentStatus
from tests.integration.test_requisitos_documentales import _carga, _entorno, _permisos

pytestmark = pytest.mark.integration


async def _publicar(session: AsyncSession, *, evento: str = "prueba.ocurrio", dedup=None):
    return await outbox.publicar(
        session,
        aggregate_type="prueba",
        aggregate_id=uuid.uuid4(),
        event_type=evento,
        payload={"dato": "valor"},
        dedup_key=dedup,
    )


async def _fila(session: AsyncSession, evento_id: uuid.UUID):
    return (
        await session.execute(
            text("""
                SELECT status, attempt_count, processed_at, available_at, last_error_code
                FROM outbox_events WHERE id = :id
            """),
            {"id": evento_id},
        )
    ).one()


async def _pendientes(session: AsyncSession) -> int:
    return (
        await session.execute(text("SELECT count(*) FROM outbox_events WHERE status = 'PENDING'"))
    ).scalar_one()


class TestEscrituraTransaccional:
    async def test_el_evento_viaja_con_el_cambio_de_negocio(self, session: AsyncSession) -> None:
        evento_id = await _publicar(session)

        fila = await _fila(session, evento_id)
        assert fila.status == "PENDING"
        assert fila.processed_at is None
        assert fila.attempt_count == 0

    async def test_publicar_no_commitea(self, migrated_database: str) -> None:
        """Si la transacción de negocio se revierte, el evento se va con ella.

        Es la razón de ser del patrón: el legacy mandaba el correo desde
        `on_commit` y si el proceso moría en el medio, la notificación se
        perdía. Acá o quedan los dos o no queda ninguno.
        """
        engine = create_async_engine(migrated_database)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        marca = f"rollback-{uuid.uuid4().hex}"

        try:
            async with factory() as s:
                await _publicar(s, dedup=marca)
                await s.rollback()

            async with factory() as verificacion:
                quedo = (
                    await verificacion.execute(
                        text("SELECT count(*) FROM outbox_events WHERE dedup_key = :k"),
                        {"k": marca},
                    )
                ).scalar_one()

            assert quedo == 0
        finally:
            await engine.dispose()

    async def test_la_clave_de_deduplicacion_impide_el_doble_disparo(
        self, session: AsyncSession
    ) -> None:
        clave = f"dedup-{uuid.uuid4().hex}"

        primero = await _publicar(session, dedup=clave)
        segundo = await _publicar(session, dedup=clave)

        assert primero is not None
        # El segundo no crea fila: `ON CONFLICT DO NOTHING` no devuelve id.
        assert segundo is None

    async def test_sin_clave_no_hay_deduplicacion(self, session: AsyncSession) -> None:
        """El índice único es parcial: varios eventos pueden no tener clave."""
        uno = await _publicar(session)
        dos = await _publicar(session)

        assert uno != dos
        assert None not in (uno, dos)


class TestEntrega:
    async def test_un_evento_entregado_queda_done(self, session: AsyncSession) -> None:
        evento_id = await _publicar(session)
        vistos: list[uuid.UUID] = []

        async def manejador(evento):
            vistos.append(evento.id)

        resultado = await outbox.procesar_lote(session, manejador)

        assert evento_id in vistos
        assert resultado.entregados >= 1
        fila = await _fila(session, evento_id)
        assert fila.status == "DONE"
        assert fila.processed_at is not None

    async def test_un_evento_ya_procesado_no_se_vuelve_a_tomar(self, session: AsyncSession) -> None:
        """Esto es lo que impide el duplicado al reiniciar el worker."""
        evento_id = await _publicar(session)

        async def manejador(evento):
            return None

        await outbox.procesar_lote(session, manejador)

        segunda = await outbox.reclamar(session, limite=50)
        assert evento_id not in {e.id for e in segunda}

    async def test_un_evento_futuro_no_se_toma_todavia(self, session: AsyncSession) -> None:
        evento_id = await _publicar(session)
        await session.execute(
            text(
                "UPDATE outbox_events SET available_at = now() + interval '1 hour' WHERE id = :id"
            ),
            {"id": evento_id},
        )

        listos = await outbox.reclamar(session, limite=50)

        assert evento_id not in {e.id for e in listos}


class TestReintentos:
    async def test_un_fallo_reprograma_con_backoff(self, session: AsyncSession) -> None:
        evento_id = await _publicar(session)
        antes = datetime.now(UTC)

        async def manejador(evento):
            raise RuntimeError("proveedor caído")

        resultado = await outbox.procesar_lote(session, manejador)

        assert resultado.reintentar >= 1
        fila = await _fila(session, evento_id)
        assert fila.status == "PENDING"
        assert fila.attempt_count == 1
        assert fila.last_error_code == "RuntimeError"
        # Reprogramado hacia adelante: sin esto el worker giraría en vacío
        # golpeando al proveedor caído.
        assert fila.available_at > antes

    async def test_el_backoff_crece_y_tiene_techo(self) -> None:
        esperas = [outbox.calcular_backoff(i).total_seconds() for i in range(1, 8)]

        assert esperas[:5] == [60, 120, 240, 480, 960]
        # Techo: el sexto intento no cae dentro de varias horas, cuando ya nadie
        # está mirando.
        assert all(e <= outbox.TECHO_BACKOFF_SEGUNDOS for e in esperas)
        assert esperas[-1] == outbox.TECHO_BACKOFF_SEGUNDOS

    async def test_un_proveedor_caido_no_duplica(self, session: AsyncSession) -> None:
        """Reintentar no puede convertir un evento en dos notificaciones."""
        evento_id = await _publicar(session)
        entregas: list[uuid.UUID] = []

        # Solo falla el evento de ESTA prueba. Otras dejan eventos pendientes
        # en la misma transacción (cada transición de carga publica uno), y el
        # lote los arrastra: contar todo lo que pasa por el manejador mediría
        # el resto de la suite, no esto.
        async def falla_siempre(evento):
            if evento.id != evento_id:
                return
            entregas.append(evento.id)
            raise ConnectionError("smtp no responde")

        for _ in range(3):
            # Cada pasada exige adelantar el reloj: el backoff lo dejó futuro.
            await session.execute(
                text("UPDATE outbox_events SET available_at = now() WHERE id = :id"),
                {"id": evento_id},
            )
            await outbox.procesar_lote(session, falla_siempre)

        assert entregas == [evento_id] * 3
        fila = await _fila(session, evento_id)
        assert fila.attempt_count == 3
        assert fila.status == "PENDING"

    async def test_agotar_los_reintentos_no_descarta_el_evento(self, session: AsyncSession) -> None:
        """Queda en FAILED con el motivo, no desaparece."""
        evento_id = await _publicar(session)

        async def falla_siempre(evento):
            if evento.id != evento_id:
                return
            raise TimeoutError("sin respuesta")

        for _ in range(outbox.MAX_INTENTOS):
            await session.execute(
                text("UPDATE outbox_events SET available_at = now() WHERE id = :id"),
                {"id": evento_id},
            )
            await outbox.procesar_lote(session, falla_siempre)

        fila = await _fila(session, evento_id)
        assert fila.status == "FAILED"
        assert fila.attempt_count == outbox.MAX_INTENTOS
        assert fila.last_error_code == "TimeoutError"
        # Y ya no se reclama: si no, giraría para siempre.
        assert evento_id not in {e.id for e in await outbox.reclamar(session, limite=50)}

    async def test_un_fallo_no_arrastra_al_resto_del_lote(self, session: AsyncSession) -> None:
        malo = await _publicar(session, evento="prueba.falla")
        bueno = await _publicar(session, evento="prueba.ok")

        async def manejador(evento):
            if evento.id == malo:
                raise RuntimeError("este falla")
            return None

        await outbox.procesar_lote(session, manejador)

        assert (await _fila(session, malo)).status == "PENDING"
        assert (await _fila(session, bueno)).status == "DONE"


class TestVariosWorkers:
    async def test_dos_workers_no_procesan_el_mismo_evento(self, migrated_database: str) -> None:
        """`FOR UPDATE SKIP LOCKED`: cada worker se lleva filas distintas."""
        engine = create_async_engine(migrated_database)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        marca = uuid.uuid4().hex
        ids: list[uuid.UUID] = []

        try:
            async with factory() as preparacion:
                for i in range(10):
                    ids.append(await _publicar(preparacion, dedup=f"{marca}-{i}"))
                await preparacion.commit()

            reclamados: list[list[uuid.UUID]] = []

            async def worker() -> None:
                async with factory() as s:
                    # Límite alto a propósito: la base puede tener pendientes
                    # de otras pruebas, y con `limite=10` el primer worker se
                    # llevaría diez ajenos y ninguno de estos.
                    eventos = await outbox.reclamar(s, limite=1000)
                    reclamados.append([e.id for e in eventos])
                    # El lock vive mientras viva la transacción: soltarlo antes
                    # de que el otro worker reclame invalidaría la prueba.
                    await asyncio.sleep(0.2)
                    await s.commit()

            await asyncio.gather(worker(), worker())

            uno, dos = (set(r) & set(ids) for r in reclamados)
            assert uno & dos == set(), "dos workers se llevaron el mismo evento"
            assert uno | dos == set(ids)
        finally:
            async with factory() as limpieza:
                await limpieza.execute(
                    text("DELETE FROM outbox_events WHERE dedup_key LIKE :p"), {"p": f"{marca}-%"}
                )
                await limpieza.commit()
            await engine.dispose()

    async def test_el_worker_muerto_devuelve_sus_eventos(self, migrated_database: str) -> None:
        """El gate del paso: reinicio sin pérdida ni duplicado.

        Se simula la muerte reclamando y abortando la transacción sin marcar
        nada. PostgreSQL suelta el lock, y el evento vuelve a estar disponible.
        Esta es la razón por la que `status` NO tiene un valor `PROCESSING`:
        una columna persistida habría sobrevivido a la muerte del worker y el
        evento quedaría trabado para siempre.
        """
        engine = create_async_engine(migrated_database)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        clave = f"muerte-{uuid.uuid4().hex}"

        try:
            async with factory() as preparacion:
                evento_id = await _publicar(preparacion, dedup=clave)
                await preparacion.commit()

            entregas: list[uuid.UUID] = []

            async def manejador(evento):
                # Acotado al evento propio: la base puede tener pendientes que
                # commitearon otras pruebas.
                if evento.id == evento_id:
                    entregas.append(evento.id)

            # Worker 1: entrega y muere antes de commitear.
            async with factory() as muerto:
                await outbox.procesar_lote(muerto, manejador)
                await muerto.rollback()

            # Worker 2: al reiniciar lo encuentra pendiente y lo termina.
            async with factory() as revivido:
                await outbox.procesar_lote(revivido, manejador)
                await revivido.commit()

            async with factory() as verificacion:
                fila = await _fila(verificacion, evento_id)

            # Se entregó dos veces porque la primera no se persistió: por eso
            # los manejadores deben tolerar el reenvío. Lo que NO puede pasar es
            # que el evento se pierda o quede DONE sin haberse entregado.
            assert entregas == [evento_id, evento_id]
            assert fila.status == "DONE"
            assert fila.attempt_count == 1
        finally:
            async with factory() as limpieza:
                await limpieza.execute(
                    text("DELETE FROM outbox_events WHERE dedup_key = :k"), {"k": clave}
                )
                await limpieza.commit()
            await engine.dispose()


class TestCoherenciaEnBase:
    async def test_un_estado_invalido_se_rechaza(self, session: AsyncSession) -> None:
        evento_id = await _publicar(session)

        with pytest.raises(IntegrityError):
            await session.execute(
                text("UPDATE outbox_events SET status = 'ENVIANDO' WHERE id = :id"),
                {"id": evento_id},
            )

    async def test_done_sin_fecha_se_rechaza(self, session: AsyncSession) -> None:
        """El CHECK impide que el rastro quede incompleto por un bug."""
        evento_id = await _publicar(session)

        with pytest.raises(IntegrityError):
            await session.execute(
                text("UPDATE outbox_events SET status = 'DONE' WHERE id = :id"),
                {"id": evento_id},
            )


class TestProductoresReales:
    """No alcanza con que el mecanismo funcione: hay que probar que se usa."""

    async def test_una_transicion_de_carga_deja_su_evento(
        self, session: AsyncSession, redis
    ) -> None:
        ctx = await _entorno(session)
        carga = await _carga(session, ctx, estado=ShipmentStatus.IN_TRANSIT)

        await cargas.transicionar(
            session,
            shipment_id=carga,
            datos=cargas.DatosTransicion(to_status=ShipmentStatus.RECEIVED, row_version=1),
            actor_user_id=ctx["admin"],
            permisos=await _permisos(session, redis, ctx["admin"]),
        )

        fila = (
            await session.execute(
                text("""
                    SELECT event_type, payload, status FROM outbox_events
                    WHERE aggregate_id = :c AND aggregate_type = 'shipment'
                """),
                {"c": carga},
            )
        ).one()

        assert fila.event_type == "shipment.status_changed"
        assert fila.payload["hacia"] == ShipmentStatus.RECEIVED
        assert fila.status == "PENDING"

    async def test_dos_transiciones_dejan_dos_eventos_distintos(
        self, session: AsyncSession, redis
    ) -> None:
        """La clave de deduplicación usa `row_version`, que cambia en cada paso.

        Si usara solo el id de la carga, el segundo cambio de estado no
        generaría aviso.
        """
        ctx = await _entorno(session)
        carga = await _carga(session, ctx, estado=ShipmentStatus.IN_TRANSIT)

        for version, destino in ((1, ShipmentStatus.RECEIVED), (2, ShipmentStatus.STORED)):
            await cargas.transicionar(
                session,
                shipment_id=carga,
                datos=cargas.DatosTransicion(to_status=destino, row_version=version),
                actor_user_id=ctx["admin"],
                permisos=await _permisos(session, redis, ctx["admin"]),
            )

        total = (
            await session.execute(
                text("SELECT count(*) FROM outbox_events WHERE aggregate_id = :c"),
                {"c": carga},
            )
        ).scalar_one()

        assert total == 2

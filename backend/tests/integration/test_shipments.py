"""Tabla `shipments`: identidad, concurrencia y constraints (Paso 2.2)."""

import asyncio
import re
import uuid

import pytest
from scripts.seed_rbac import sembrar as sembrar_rbac
from scripts.seed_shipment_statuses import sembrar as sembrar_estados
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.piezas import sembrar_pieza

pytestmark = pytest.mark.integration


async def _preparar(session: AsyncSession) -> dict[str, uuid.UUID]:
    """Siembra catálogos y crea empresa, usuario y ubicaciones mínimas."""
    await sembrar_rbac(session)
    await sembrar_estados(session)

    company_id = (
        await session.execute(
            text("""
                INSERT INTO companies (legal_name, status)
                VALUES ('Importadora Prueba S.A.', 'ACTIVE') RETURNING id
            """)
        )
    ).scalar_one()

    user_id = (
        await session.execute(
            text("""
                INSERT INTO users (email, password_hash, first_name, last_name, status)
                VALUES (:email, 'h', 'N', 'A', 'ACTIVE') RETURNING id
            """),
            {"email": f"ship-{uuid.uuid4().hex[:8]}@amvarmar.com"},
        )
    ).scalar_one()

    # Upsert: los tests de concurrencia confirman sus datos, así que esta
    # función tiene que poder correr contra una base que ya los tiene.
    miami = await _ubicacion(session, "US", "MIA", "Miami")
    destino = await _ubicacion(session, "CR", "SJO", "San José")

    bodega = (
        await session.execute(
            text("""
                INSERT INTO facilities
                    (location_id, facility_code, facility_type, uses_warehouse_receipt)
                VALUES (:loc, 'MIA-WH-01', 'WAREHOUSE', true)
                ON CONFLICT (facility_code) DO UPDATE SET is_active = true
                RETURNING id
            """),
            {"loc": miami},
        )
    ).scalar_one()

    return {
        "company_id": company_id,
        "user_id": user_id,
        "origen": miami,
        "destino": destino,
        "bodega": bodega,
    }


async def _ubicacion(session: AsyncSession, pais: str, ciudad: str, nombre: str) -> uuid.UUID:
    return (
        await session.execute(
            text("""
                INSERT INTO locations (country_code, city_code, location_code, name)
                VALUES (:pais, :ciudad, :codigo, :nombre)
                ON CONFLICT (location_code) DO UPDATE SET name = EXCLUDED.name
                RETURNING id
            """),
            {
                "pais": pais,
                "ciudad": ciudad,
                "codigo": f"{pais}-{ciudad}",
                "nombre": nombre,
            },
        )
    ).scalar_one()


async def _crear_shipment(session: AsyncSession, ctx: dict[str, uuid.UUID], **extra: object):
    columnas = {
        "company_id": ctx["company_id"],
        "created_by": ctx["user_id"],
        "current_status_code": "PRE_ALERT",
        "origin_location_id": ctx["origen"],
        "destination_location_id": ctx["destino"],
        **extra,
    }
    # Los nombres de columna se interpolan porque no se pueden parametrizar en
    # SQL; salen de literales de este archivo, nunca de entrada externa. Los
    # VALORES sí van parametrizados.
    nombres = ", ".join(columnas)
    valores = ", ".join(f":{c}" for c in columnas)
    consulta = f"INSERT INTO shipments ({nombres}) VALUES ({valores}) RETURNING id, shipment_number"  # noqa: S608
    fila = (await session.execute(text(consulta), columnas)).one()

    # Toda carga activa necesita al menos una pieza.
    await sembrar_pieza(session, fila.id)
    return fila


class TestIdentidad:
    async def test_el_numero_se_genera_con_el_formato_acordado(self, session: AsyncSession) -> None:
        ctx = await _preparar(session)

        fila = await _crear_shipment(session, ctx)

        assert re.fullmatch(r"SHP-\d{4}-\d{6}", fila.shipment_number)

    async def test_el_numero_no_es_la_clave_primaria(self, session: AsyncSession) -> None:
        """Diferencia central con el legacy, donde `wr_number` era la PK."""
        pk = (
            await session.execute(
                text("""
                    SELECT a.attname FROM pg_index i
                    JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
                    WHERE i.indrelid = 'shipments'::regclass AND i.indisprimary
                """)
            )
        ).scalar_one()

        assert pk == "id"

    async def test_dos_cargas_no_comparten_numero(self, session: AsyncSession) -> None:
        ctx = await _preparar(session)

        primera = await _crear_shipment(session, ctx)
        segunda = await _crear_shipment(session, ctx)

        assert primera.shipment_number != segunda.shipment_number

    async def test_no_se_puede_repetir_un_numero(self, session: AsyncSession) -> None:
        ctx = await _preparar(session)
        existente = await _crear_shipment(session, ctx)

        with pytest.raises(IntegrityError):
            await _crear_shipment(session, ctx, shipment_number=existente.shipment_number)


class TestConstraints:
    async def test_estado_inexistente_rechazado(self, session: AsyncSession) -> None:
        ctx = await _preparar(session)

        with pytest.raises(IntegrityError):
            await _crear_shipment(session, ctx, current_status_code="INVENTADO")

    @pytest.mark.parametrize(
        ("columna", "valor"),
        [
            ("weight_kg", -1),
            ("volumetric_weight_kg", -1),
            ("volume_m3", -1),
            ("package_count", -1),
        ],
    )
    async def test_medidas_no_pueden_ser_negativas(
        self, session: AsyncSession, columna: str, valor: int
    ) -> None:
        ctx = await _preparar(session)

        with pytest.raises(IntegrityError):
            await _crear_shipment(session, ctx, **{columna: valor})

    async def test_modo_de_transporte_invalido_rechazado(self, session: AsyncSession) -> None:
        ctx = await _preparar(session)

        with pytest.raises(IntegrityError):
            await _crear_shipment(session, ctx, transport_mode="TELETRANSPORTE")

    async def test_legal_hold_exige_motivo(self, session: AsyncSession) -> None:
        """ADR-0007: un hold sin motivo no se puede auditar después."""
        ctx = await _preparar(session)

        with pytest.raises(IntegrityError):
            await _crear_shipment(session, ctx, legal_hold=True)

    async def test_legal_hold_con_motivo_se_acepta(self, session: AsyncSession) -> None:
        ctx = await _preparar(session)

        fila = await _crear_shipment(
            session, ctx, legal_hold=True, legal_hold_reason="Disputa comercial abierta"
        )

        assert fila.id is not None

    async def test_no_se_puede_borrar_una_empresa_con_cargas(self, session: AsyncSession) -> None:
        ctx = await _preparar(session)
        await _crear_shipment(session, ctx)

        with pytest.raises(IntegrityError):
            await session.execute(
                text("DELETE FROM companies WHERE id = :id"), {"id": ctx["company_id"]}
            )

    async def test_el_origen_es_obligatorio(self, session: AsyncSession) -> None:
        """ADR-0005: no hay carga sin ubicación de origen estructurada."""
        ctx = await _preparar(session)

        with pytest.raises(IntegrityError):
            await session.execute(
                text("""
                    INSERT INTO shipments
                        (company_id, created_by, current_status_code, destination_location_id)
                    VALUES (:c, :u, 'PRE_ALERT', :d)
                """),
                {"c": ctx["company_id"], "u": ctx["user_id"], "d": ctx["destino"]},
            )


class TestCamposDeLosADR:
    async def test_arranca_sin_banderas_activas(self, session: AsyncSession) -> None:
        ctx = await _preparar(session)
        fila = await _crear_shipment(session, ctx)

        estado = (
            await session.execute(
                text("""
                    SELECT legacy_review_required, permit_review_required, legal_hold,
                           archived_at, legacy_status, row_version
                    FROM shipments WHERE id = :id
                """),
                {"id": fila.id},
            )
        ).one()

        assert estado.legacy_review_required is False
        assert estado.permit_review_required is False
        assert estado.legal_hold is False
        assert estado.archived_at is None
        # NULL en cargas nativas; solo las migradas llevan valor (ADR-0002).
        assert estado.legacy_status is None
        assert estado.row_version == 1

    async def test_una_carga_migrada_conserva_su_estado_legacy(self, session: AsyncSession) -> None:
        ctx = await _preparar(session)

        fila = await _crear_shipment(
            session, ctx, legacy_status="PENDIENTE", legacy_review_required=True
        )

        guardado = (
            await session.execute(
                text("SELECT legacy_status FROM shipments WHERE id = :id"), {"id": fila.id}
            )
        ).scalar_one()
        assert guardado == "PENDIENTE"


class TestConcurrencia:
    async def test_cien_cargas_simultaneas_no_colisionan(self, migrated_database: str) -> None:
        """`nextval` es atómico: 100 inserciones en paralelo dan 100 números."""
        engine = create_async_engine(migrated_database)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        marca = uuid.uuid4().hex[:8]

        async with factory() as preparacion:
            ctx = await _preparar(preparacion)
            await preparacion.execute(
                text("UPDATE companies SET legal_name = :n WHERE id = :id"),
                {"n": f"Concurrencia {marca}", "id": ctx["company_id"]},
            )
            await preparacion.commit()

        async def crear() -> str:
            async with factory() as s:
                fila = await _crear_shipment(s, ctx)
                await s.commit()
                return str(fila.shipment_number)

        try:
            numeros = await asyncio.gather(*[crear() for _ in range(100)])

            assert len(numeros) == 100
            assert len(set(numeros)) == 100, "hubo números repetidos"
        finally:
            async with factory() as limpieza:
                await limpieza.execute(
                    text("DELETE FROM shipments WHERE company_id = :c"),
                    {"c": ctx["company_id"]},
                )
                await limpieza.commit()
            await engine.dispose()

    async def test_dos_ediciones_con_el_mismo_row_version(self, migrated_database: str) -> None:
        """Bloqueo optimista: una pasa, la otra no encuentra fila que actualizar."""
        engine = create_async_engine(migrated_database)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        async with factory() as preparacion:
            ctx = await _preparar(preparacion)
            fila = await _crear_shipment(preparacion, ctx)
            await preparacion.commit()
            shipment_id = fila.id

        async def editar(descripcion: str) -> bool:
            async with factory() as s:
                resultado = await s.execute(
                    text("""
                        UPDATE shipments
                        SET description = :d, row_version = row_version + 1
                        WHERE id = :id AND row_version = 1
                        RETURNING id
                    """),
                    {"d": descripcion, "id": shipment_id},
                )
                gano = resultado.one_or_none() is not None
                await s.commit()
                return gano

        try:
            resultados = await asyncio.gather(editar("primera"), editar("segunda"))

            assert sorted(resultados) == [
                False,
                True,
            ], f"se esperaba exactamente un ganador, salió {resultados}"

            async with factory() as verificacion:
                version = (
                    await verificacion.execute(
                        text("SELECT row_version FROM shipments WHERE id = :id"),
                        {"id": shipment_id},
                    )
                ).scalar_one()
            # Solo una edición se aplicó, así que la versión subió una vez.
            assert version == 2
        finally:
            async with factory() as limpieza:
                await limpieza.execute(
                    text("DELETE FROM shipments WHERE company_id = :c"),
                    {"c": ctx["company_id"]},
                )
                await limpieza.commit()
            await engine.dispose()

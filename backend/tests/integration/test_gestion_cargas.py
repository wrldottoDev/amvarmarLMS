"""Alta y edición de cargas por Operaciones.

Es lo que en el sistema viejo se hacía creando un `Warehouse`. Lo que estas
pruebas vigilan es la frontera con el motor de transiciones: acá se corrigen
datos, nunca se mueve el estado.
"""

import uuid
from decimal import Decimal

import pytest
from scripts.seed_rbac import sembrar as sembrar_rbac
from scripts.seed_shipment_statuses import sembrar as sembrar_estados
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.rbac.models import RoleCode, ScopeType
from app.modules.rbac.service import obtener_permisos_efectivos
from app.modules.shipments import gestion
from app.modules.shipments.models import ShipmentStatus
from app.modules.shipments.policies import IdentificadorFaltante
from app.modules.shipments.service import SinPermisoParaTransicion, VersionDesactualizada

pytestmark = pytest.mark.integration


@pytest.fixture
async def entorno(session: AsyncSession):
    await sembrar_rbac(session)
    await sembrar_estados(session)

    empresa = (
        await session.execute(
            text("INSERT INTO companies (legal_name, status) VALUES (:n,'ACTIVE') RETURNING id"),
            {"n": f"Alta {uuid.uuid4().hex[:6]} S.A."},
        )
    ).scalar_one()

    async def usuario(rol: str, alcance: str, company) -> uuid.UUID:
        uid = (
            await session.execute(
                text("""
                    INSERT INTO users (email, password_hash, first_name, last_name, status)
                    VALUES (:e,'h','N','A','ACTIVE') RETURNING id
                """),
                {"e": f"g-{uuid.uuid4().hex[:10]}@pruebas.amvarmar.com"},
            )
        ).scalar_one()
        await session.execute(
            text("""
                INSERT INTO user_role_assignments (user_id, role_id, scope_type, company_id)
                SELECT :u, r.id, :s, :c FROM roles r WHERE r.code = :rol
            """),
            {"u": uid, "rol": rol, "s": alcance, "c": company},
        )
        if company is not None:
            await session.execute(
                text(
                    "INSERT INTO company_memberships (company_id, user_id, status) "
                    "VALUES (:c,:u,'ACTIVE')"
                ),
                {"c": company, "u": uid},
            )
        return uid

    async def ubicacion(pais: str, ciudad: str) -> uuid.UUID:
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

    return {
        "empresa": empresa,
        "ops": await usuario(RoleCode.OPS_ADMIN, ScopeType.GLOBAL, None),
        "cliente": await usuario(RoleCode.CLIENT_USER, ScopeType.ORGANIZATION, empresa),
        "origen": await ubicacion("US", "MIA"),
        "destino": await ubicacion("CR", "SJO"),
    }


async def _permisos(session, redis, user_id):
    return await obtener_permisos_efectivos(session, redis, user_id)


def _datos(entorno, **extra) -> gestion.DatosDeCarga:
    # Lleva factura porque el origen por defecto no emite Warehouse Receipt: la
    # regla del negocio es que lo de Miami va por WR y todo lo demás por factura.
    extra.setdefault("invoice", f"INV-{uuid.uuid4().hex[:8].upper()}")
    # Y peso, que el alta exige igual que lo exigía el sistema viejo: sin él la
    # carga entra al inventario como un bulto de masa desconocida.
    extra.setdefault("weight_kg", Decimal("10"))
    return gestion.DatosDeCarga(
        company_id=entorno["empresa"],
        origin_location_id=entorno["origen"],
        destination_location_id=entorno["destino"],
        **extra,
    )


class TestCrear:
    async def test_nace_en_prealerta_con_numero_propio(
        self, session: AsyncSession, redis, entorno
    ) -> None:
        creada = await gestion.crear(
            session,
            datos=_datos(entorno, description="Repuestos"),
            actor_user_id=entorno["ops"],
            permisos=await _permisos(session, redis, entorno["ops"]),
        )

        assert creada.status == ShipmentStatus.PRE_ALERT
        assert creada.shipment_number.startswith("SHP-")
        assert creada.row_version == 1

    async def test_deja_su_evento_de_alta(self, session: AsyncSession, redis, entorno) -> None:
        """Sin evento, la carga aparecería sin explicación en la línea de tiempo."""
        creada = await gestion.crear(
            session,
            datos=_datos(entorno),
            actor_user_id=entorno["ops"],
            permisos=await _permisos(session, redis, entorno["ops"]),
        )

        evento = (
            await session.execute(
                text("SELECT event_type, title FROM shipment_events WHERE shipment_id = :s"),
                {"s": creada.id},
            )
        ).one()
        assert evento.event_type == "CREATED"
        assert evento.title == "Carga creada"

    async def test_sin_factura_y_sin_bodega_wr_se_rechaza(
        self, session: AsyncSession, redis, entorno
    ) -> None:
        """La regla del negocio: lo de Miami lleva WR, lo demás lleva factura.

        Sin ninguno de los dos, la carga solo se puede encontrar por su número
        interno — un dato que el cliente no conoce y que no aparece en ningún
        papel del embarque.
        """
        with pytest.raises(IdentificadorFaltante):
            await gestion.crear(
                session,
                datos=gestion.DatosDeCarga(
                    company_id=entorno["empresa"],
                    origin_location_id=entorno["origen"],
                    destination_location_id=entorno["destino"],
                    weight_kg=Decimal("10"),
                ),
                actor_user_id=entorno["ops"],
                permisos=await _permisos(session, redis, entorno["ops"]),
            )

    async def test_desde_una_bodega_que_emite_wr_no_hace_falta_factura(
        self, session: AsyncSession, redis, entorno
    ) -> None:
        """El WR lo emite la bodega al recibir, así que puede no existir todavía
        al dar de alta. Se exige al almacenar, no acá."""
        bodega = (
            await session.execute(
                text("""
                    INSERT INTO facilities
                        (location_id, facility_code, facility_type, uses_warehouse_receipt)
                    VALUES (:l, :cod, 'WAREHOUSE', true) RETURNING id
                """),
                {"l": entorno["origen"], "cod": f"MIA-{uuid.uuid4().hex[:5]}"},
            )
        ).scalar_one()

        creada = await gestion.crear(
            session,
            datos=gestion.DatosDeCarga(
                company_id=entorno["empresa"],
                origin_location_id=entorno["origen"],
                destination_location_id=entorno["destino"],
                origin_facility_id=bodega,
                weight_kg=Decimal("10"),
            ),
            actor_user_id=entorno["ops"],
            permisos=await _permisos(session, redis, entorno["ops"]),
        )

        assert creada.id

    async def test_sin_peso_no_se_crea(self, session: AsyncSession, redis, entorno) -> None:
        """Regla del `WarehouseForm.clean` del sistema viejo.

        Sin peso no se puede cotizar ni consolidar, y la carga entra al
        inventario como un bulto de masa desconocida.
        """
        with pytest.raises(gestion.DatosInvalidos):
            await gestion.crear(
                session,
                datos=gestion.DatosDeCarga(
                    company_id=entorno["empresa"],
                    origin_location_id=entorno["origen"],
                    destination_location_id=entorno["destino"],
                    invoice="INV-SIN-PESO",
                ),
                actor_user_id=entorno["ops"],
                permisos=await _permisos(session, redis, entorno["ops"]),
            )

    async def test_con_libras_alcanza(self, session: AsyncSession, redis, entorno) -> None:
        """Uno de los dos, no los dos: el viejo pedía `lbs` o `kgs`."""
        creada = await gestion.crear(
            session,
            datos=_datos(entorno, weight_kg=None, weight_lb=Decimal("550")),
            actor_user_id=entorno["ops"],
            permisos=await _permisos(session, redis, entorno["ops"]),
        )

        assert creada.id

    async def test_guarda_las_piezas(self, session: AsyncSession, redis, entorno) -> None:
        """La sección "Tipos de carga" del alta del sistema viejo.

        Van en el mismo cuerpo que la carga y no en una llamada aparte: si la
        segunda fallara quedaría una carga sin su desglose y nadie se enteraría.
        """
        creada = await gestion.crear(
            session,
            datos=_datos(
                entorno,
                packages=(
                    gestion.DatosDeBulto(package_type="PALLET", quantity=3, description="Cajas"),
                    gestion.DatosDeBulto(package_type="DRUM", quantity=2),
                ),
            ),
            actor_user_id=entorno["ops"],
            permisos=await _permisos(session, redis, entorno["ops"]),
        )

        piezas = (
            await session.execute(
                text("""
                    SELECT package_type, quantity, description FROM shipment_packages
                    WHERE shipment_id = :s ORDER BY package_type
                """),
                {"s": creada.id},
            )
        ).all()

        assert [(f.package_type, f.quantity) for f in piezas] == [("DRUM", 2), ("PALLET", 3)]
        assert piezas[1].description == "Cajas"

    async def test_una_pieza_de_tipo_inventado_se_rechaza(
        self, session: AsyncSession, redis, entorno
    ) -> None:
        with pytest.raises(gestion.DatosInvalidos):
            await gestion.crear(
                session,
                datos=_datos(
                    entorno,
                    packages=(gestion.DatosDeBulto(package_type="CONTENEDOR", quantity=1),),
                ),
                actor_user_id=entorno["ops"],
                permisos=await _permisos(session, redis, entorno["ops"]),
            )

    async def test_puede_nacer_almacenada_con_sus_hitos(
        self, session: AsyncSession, redis, entorno
    ) -> None:
        """Quien recibe en mostrador no debería crear en prealerta y avanzar a mano.

        Se comprueban las dos consecuencias: que queden las fechas de los hitos
        que ese estado da por cumplidos, y que la línea de tiempo explique por
        qué arranca ahí en vez de parecer que se saltó tres pasos.
        """
        creada = await gestion.crear(
            session,
            datos=_datos(entorno, initial_status="STORED"),
            actor_user_id=entorno["ops"],
            permisos=await _permisos(session, redis, entorno["ops"]),
        )

        assert creada.status == "STORED"

        fila = (
            await session.execute(
                text("SELECT received_at, stored_at FROM shipments WHERE id = :s"),
                {"s": creada.id},
            )
        ).one()
        assert fila.received_at is not None
        assert fila.stored_at is not None

        titulos = (
            (
                await session.execute(
                    text("""
                        SELECT title FROM shipment_events
                        WHERE shipment_id = :s ORDER BY occurred_at, id
                    """),
                    {"s": creada.id},
                )
            )
            .scalars()
            .all()
        )
        assert "Registrada con la mercancía ya presente" in titulos

    async def test_no_puede_nacer_despachada(self, session: AsyncSession, redis, entorno) -> None:
        """Una carga no empieza su vida ya despachada.

        Permitirlo dejaría cargas sin ningún registro de haber estado en bodega,
        que es justo lo que la línea de tiempo existe para evitar.
        """
        with pytest.raises(gestion.DatosInvalidos):
            await gestion.crear(
                session,
                datos=_datos(entorno, initial_status="DISPATCHED"),
                actor_user_id=entorno["ops"],
                permisos=await _permisos(session, redis, entorno["ops"]),
            )

    async def test_el_wr_se_guarda_como_referencia(
        self, session: AsyncSession, redis, entorno
    ) -> None:
        """Lo de Miami se identifica por WR y se puede escribir al dar de alta."""
        creada = await gestion.crear(
            session,
            datos=_datos(entorno, wr="WR105921"),
            actor_user_id=entorno["ops"],
            permisos=await _permisos(session, redis, entorno["ops"]),
        )

        valor = (
            await session.execute(
                text("""
                    SELECT value FROM shipment_references
                    WHERE shipment_id = :s AND reference_type = 'WR'
                """),
                {"s": creada.id},
            )
        ).scalar_one()

        assert valor == "WR105921"

    async def test_guarda_los_campos_comerciales_del_sistema_viejo(
        self, session: AsyncSession, redis, entorno
    ) -> None:
        """Shipper, carrier, CFTS y libras eran columnas del listado viejo.

        Dentro de la descripción no se pueden buscar ni ordenar, que es para lo
        que se usan.
        """
        creada = await gestion.crear(
            session,
            datos=_datos(
                entorno,
                shipper="Proveedor Ejemplo",
                carrier="Naviera Ejemplo",
                foots_cft=Decimal("42.50"),
                weight_lb=Decimal("120.000"),
                weight_kg=Decimal("54.431"),
                tracking="1Z999",
                po="PO-77",
                container="MSCU1234567",
            ),
            actor_user_id=entorno["ops"],
            permisos=await _permisos(session, redis, entorno["ops"]),
        )

        fila = (
            await session.execute(
                text("""
                    SELECT shipper, carrier, foots_cft, weight_lb, weight_kg
                    FROM shipments WHERE id = :s
                """),
                {"s": creada.id},
            )
        ).one()
        assert fila.shipper == "Proveedor Ejemplo"
        assert fila.carrier == "Naviera Ejemplo"
        assert fila.foots_cft == Decimal("42.50")
        # Kilos y libras se guardan por separado: no se calcula uno del otro.
        assert fila.weight_lb == Decimal("120.000")
        assert fila.weight_kg == Decimal("54.431")

        referencias = {
            f.reference_type: f.value
            for f in (
                await session.execute(
                    text(
                        "SELECT reference_type, value FROM shipment_references WHERE shipment_id = :s"
                    ),
                    {"s": creada.id},
                )
            ).all()
        }
        assert referencias["TRACKING"] == "1Z999"
        assert referencias["PO"] == "PO-77"
        assert referencias["CONTAINER"] == "MSCU1234567"

    async def test_origen_y_destino_no_pueden_coincidir(
        self, session: AsyncSession, redis, entorno
    ) -> None:
        with pytest.raises(gestion.DatosInvalidos):
            await gestion.crear(
                session,
                datos=gestion.DatosDeCarga(
                    company_id=entorno["empresa"],
                    origin_location_id=entorno["origen"],
                    destination_location_id=entorno["origen"],
                ),
                actor_user_id=entorno["ops"],
                permisos=await _permisos(session, redis, entorno["ops"]),
            )

    async def test_una_empresa_inexistente_se_rechaza_con_mensaje_claro(
        self, session: AsyncSession, redis, entorno
    ) -> None:
        """Sin esto llegaría como violación de clave foránea, o sea un 500."""
        with pytest.raises(gestion.DatosInvalidos):
            await gestion.crear(
                session,
                datos=gestion.DatosDeCarga(
                    company_id=uuid.uuid4(),
                    origin_location_id=entorno["origen"],
                    destination_location_id=entorno["destino"],
                ),
                actor_user_id=entorno["ops"],
                permisos=await _permisos(session, redis, entorno["ops"]),
            )

    async def test_un_cliente_no_crea_cargas_de_otra_empresa(
        self, session: AsyncSession, redis, entorno
    ) -> None:
        otra = (
            await session.execute(
                text(
                    "INSERT INTO companies (legal_name, status) "
                    "VALUES ('Ajena S.A.','ACTIVE') RETURNING id"
                )
            )
        ).scalar_one()

        with pytest.raises(SinPermisoParaTransicion):
            await gestion.crear(
                session,
                datos=gestion.DatosDeCarga(
                    company_id=otra,
                    origin_location_id=entorno["origen"],
                    destination_location_id=entorno["destino"],
                ),
                actor_user_id=entorno["cliente"],
                permisos=await _permisos(session, redis, entorno["cliente"]),
            )


class TestActualizar:
    async def _crear(self, session, redis, entorno):
        return await gestion.crear(
            session,
            datos=_datos(entorno, description="Original"),
            actor_user_id=entorno["ops"],
            permisos=await _permisos(session, redis, entorno["ops"]),
        )

    async def test_corrige_solo_lo_enviado(self, session: AsyncSession, redis, entorno) -> None:
        carga = await self._crear(session, redis, entorno)

        version = await gestion.actualizar(
            session,
            shipment_id=carga.id,
            cambios={"description": "Corregido"},
            row_version=carga.row_version,
            actor_user_id=entorno["ops"],
            permisos=await _permisos(session, redis, entorno["ops"]),
        )

        fila = (
            await session.execute(
                text("SELECT description, destination_address FROM shipments WHERE id = :s"),
                {"s": carga.id},
            )
        ).one()
        assert version == carga.row_version + 1
        assert fila.description == "Corregido"
        # Lo que no se mandó no se tocó.
        assert fila.destination_address is None

    async def test_dos_ediciones_simultaneas_una_gana(
        self, session: AsyncSession, redis, entorno
    ) -> None:
        """Sin bloqueo optimista, la segunda pisa a la primera en silencio."""
        carga = await self._crear(session, redis, entorno)
        permisos = await _permisos(session, redis, entorno["ops"])

        await gestion.actualizar(
            session,
            shipment_id=carga.id,
            cambios={"description": "Primera"},
            row_version=carga.row_version,
            actor_user_id=entorno["ops"],
            permisos=permisos,
        )

        with pytest.raises(VersionDesactualizada):
            await gestion.actualizar(
                session,
                shipment_id=carga.id,
                cambios={"description": "Segunda"},
                row_version=carga.row_version,
                actor_user_id=entorno["ops"],
                permisos=permisos,
            )

    async def test_no_se_puede_cambiar_el_estado_desde_aca(
        self, session: AsyncSession, redis, entorno
    ) -> None:
        """El estado lo mueve el motor de transiciones, que valida y audita."""
        carga = await self._crear(session, redis, entorno)

        with pytest.raises(gestion.DatosInvalidos):
            await gestion.actualizar(
                session,
                shipment_id=carga.id,
                cambios={"current_status_code": "DELIVERED"},
                row_version=carga.row_version,
                actor_user_id=entorno["ops"],
                permisos=await _permisos(session, redis, entorno["ops"]),
            )

    async def test_una_carga_despachada_ya_no_se_edita(
        self, session: AsyncSession, redis, entorno
    ) -> None:
        """Sus datos describen algo que ya ocurrió: cambiarlos reescribe la
        historia en vez de corregir una captura."""
        carga = await self._crear(session, redis, entorno)
        await session.execute(
            text("UPDATE shipments SET current_status_code = 'DISPATCHED' WHERE id = :s"),
            {"s": carga.id},
        )

        with pytest.raises(gestion.NoSePuedeEditar):
            await gestion.actualizar(
                session,
                shipment_id=carga.id,
                cambios={"description": "Tarde"},
                row_version=carga.row_version,
                actor_user_id=entorno["ops"],
                permisos=await _permisos(session, redis, entorno["ops"]),
            )

    async def test_deja_constancia_de_que_se_corrigio(
        self, session: AsyncSession, redis, entorno
    ) -> None:
        carga = await self._crear(session, redis, entorno)

        await gestion.actualizar(
            session,
            shipment_id=carga.id,
            cambios={"description": "Corregido", "weight_kg": 12},
            row_version=carga.row_version,
            actor_user_id=entorno["ops"],
            permisos=await _permisos(session, redis, entorno["ops"]),
        )

        nota = (
            await session.execute(
                text("""
                    SELECT title, description FROM shipment_events
                    WHERE shipment_id = :s AND event_type = 'NOTE'
                """),
                {"s": carga.id},
            )
        ).one()
        assert nota.title == "Datos corregidos"
        assert "description" in nota.description
        assert "weight_kg" in nota.description


class TestRevisionLegacy:
    """Cargas que la migración no supo traducir con certeza (ADR-0002).

    Sin forma de resolverlas quedan como deuda invisible: el número nunca baja y
    la Fase 5 no cierra.
    """

    async def _marcada(self, session: AsyncSession, redis, entorno) -> uuid.UUID:
        carga = await gestion.crear(
            session,
            datos=_datos(entorno),
            actor_user_id=entorno["ops"],
            permisos=await _permisos(session, redis, entorno["ops"]),
        )
        await session.execute(
            text("""
                UPDATE shipments
                SET legacy_review_required = true, legacy_status = 'PENDIENTE'
                WHERE id = :s
            """),
            {"s": carga.id},
        )
        return carga.id

    async def test_las_lista_con_su_motivo(self, session: AsyncSession, redis, entorno) -> None:
        await self._marcada(session, redis, entorno)

        cargas = await gestion.listar_en_revision(
            session, permisos=await _permisos(session, redis, entorno["ops"])
        )

        assert len(cargas) == 1
        assert cargas[0].legacy_status == "PENDIENTE"

    async def test_resolver_quita_la_marca_y_deja_constancia(
        self, session: AsyncSession, redis, entorno
    ) -> None:
        carga = await self._marcada(session, redis, entorno)

        await gestion.resolver_revision(
            session,
            shipment_id=carga,
            nota="Confirmado con Operaciones: llegó el 3 de marzo.",
            actor_user_id=entorno["ops"],
            permisos=await _permisos(session, redis, entorno["ops"]),
        )

        fila = (
            await session.execute(
                text("SELECT legacy_review_required, legacy_status FROM shipments WHERE id = :s"),
                {"s": carga},
            )
        ).one()
        assert fila.legacy_review_required is False
        # El rastro del sistema viejo no se borra nunca: sirve para auditar la
        # traducción años después.
        assert fila.legacy_status == "PENDIENTE"

        evento = (
            await session.execute(
                text("""
                    SELECT title, description FROM shipment_events
                    WHERE shipment_id = :s AND event_type = 'CORRECTION'
                """),
                {"s": carga},
            )
        ).one()
        assert "3 de marzo" in evento.description

    async def test_sin_nota_no_se_resuelve(self, session: AsyncSession, redis, entorno) -> None:
        """Una marca quitada sin explicación no se puede auditar después."""
        carga = await self._marcada(session, redis, entorno)

        with pytest.raises(gestion.DatosInvalidos):
            await gestion.resolver_revision(
                session,
                shipment_id=carga,
                nota="   ",
                actor_user_id=entorno["ops"],
                permisos=await _permisos(session, redis, entorno["ops"]),
            )

    async def test_resolver_no_cambia_el_estado(
        self, session: AsyncSession, redis, entorno
    ) -> None:
        """Si además hay que corregirlo, eso va por una transición, que valida
        el catálogo y deja su propio evento."""
        carga = await self._marcada(session, redis, entorno)
        antes = (
            await session.execute(
                text("SELECT current_status_code FROM shipments WHERE id = :s"), {"s": carga}
            )
        ).scalar_one()

        await gestion.resolver_revision(
            session,
            shipment_id=carga,
            nota="Revisado.",
            actor_user_id=entorno["ops"],
            permisos=await _permisos(session, redis, entorno["ops"]),
        )

        despues = (
            await session.execute(
                text("SELECT current_status_code FROM shipments WHERE id = :s"), {"s": carga}
            )
        ).scalar_one()
        assert despues == antes

    async def test_una_carga_no_marcada_no_se_resuelve_dos_veces(
        self, session: AsyncSession, redis, entorno
    ) -> None:
        carga = await self._marcada(session, redis, entorno)
        permisos = await _permisos(session, redis, entorno["ops"])
        await gestion.resolver_revision(
            session,
            shipment_id=carga,
            nota="Revisado.",
            actor_user_id=entorno["ops"],
            permisos=permisos,
        )

        with pytest.raises(gestion.NoSePuedeEditar):
            await gestion.resolver_revision(
                session,
                shipment_id=carga,
                nota="Otra vez.",
                actor_user_id=entorno["ops"],
                permisos=permisos,
            )

    async def test_un_cliente_no_puede_resolverlas(
        self, session: AsyncSession, redis, entorno
    ) -> None:
        """Decidir qué pasó de verdad con una carga migrada es de Operaciones."""
        carga = await self._marcada(session, redis, entorno)

        with pytest.raises(SinPermisoParaTransicion):
            await gestion.resolver_revision(
                session,
                shipment_id=carga,
                nota="Yo digo que está bien.",
                actor_user_id=entorno["cliente"],
                permisos=await _permisos(session, redis, entorno["cliente"]),
            )


class TestOcultar:
    """Reemplaza al "eliminar" del sistema viejo (ADR-0007)."""

    async def _crear(self, session, redis, entorno):
        return await gestion.crear(
            session,
            datos=_datos(entorno),
            actor_user_id=entorno["ops"],
            permisos=await _permisos(session, redis, entorno["ops"]),
        )

    async def test_ocultar_no_borra_nada(self, session: AsyncSession, redis, entorno) -> None:
        carga = await self._crear(session, redis, entorno)

        await gestion.ocultar(
            session,
            shipment_id=carga.id,
            motivo="Duplicada por error de captura.",
            actor_user_id=entorno["ops"],
            permisos=await _permisos(session, redis, entorno["ops"]),
        )

        fila = (
            await session.execute(
                text("SELECT hidden_at, hidden_by, hidden_reason FROM shipments WHERE id = :s"),
                {"s": carga.id},
            )
        ).one()
        assert fila.hidden_at is not None
        # Quién la ocultó y por qué: una carga que desaparece sin explicación es
        # indistinguible de una que se perdió.
        assert fila.hidden_by == entorno["ops"]
        assert "Duplicada" in fila.hidden_reason

    async def test_sin_motivo_no_se_oculta(self, session: AsyncSession, redis, entorno) -> None:
        carga = await self._crear(session, redis, entorno)

        with pytest.raises(gestion.DatosInvalidos):
            await gestion.ocultar(
                session,
                shipment_id=carga.id,
                motivo="  ",
                actor_user_id=entorno["ops"],
                permisos=await _permisos(session, redis, entorno["ops"]),
            )

    async def test_una_carga_oculta_se_puede_recuperar(
        self, session: AsyncSession, redis, entorno
    ) -> None:
        carga = await self._crear(session, redis, entorno)
        permisos = await _permisos(session, redis, entorno["ops"])
        await gestion.ocultar(
            session,
            shipment_id=carga.id,
            motivo="Prueba.",
            actor_user_id=entorno["ops"],
            permisos=permisos,
        )

        await gestion.recuperar(
            session, shipment_id=carga.id, actor_user_id=entorno["ops"], permisos=permisos
        )

        oculta = (
            await session.execute(
                text("SELECT hidden_at FROM shipments WHERE id = :s"), {"s": carga.id}
            )
        ).scalar_one()
        assert oculta is None

    async def test_los_documentos_y_la_historia_sobreviven(
        self, session: AsyncSession, redis, entorno
    ) -> None:
        """Es la diferencia con el borrado del sistema viejo."""
        carga = await self._crear(session, redis, entorno)
        eventos_antes = (
            await session.execute(
                text("SELECT count(*) FROM shipment_events WHERE shipment_id = :s"),
                {"s": carga.id},
            )
        ).scalar_one()

        await gestion.ocultar(
            session,
            shipment_id=carga.id,
            motivo="Prueba.",
            actor_user_id=entorno["ops"],
            permisos=await _permisos(session, redis, entorno["ops"]),
        )

        eventos_despues = (
            await session.execute(
                text("SELECT count(*) FROM shipment_events WHERE shipment_id = :s"),
                {"s": carga.id},
            )
        ).scalar_one()
        # Y suma uno más: el de haberla ocultado.
        assert eventos_despues == eventos_antes + 1
        referencias = (
            await session.execute(
                text("SELECT count(*) FROM shipment_references WHERE shipment_id = :s"),
                {"s": carga.id},
            )
        ).scalar_one()
        assert referencias > 0

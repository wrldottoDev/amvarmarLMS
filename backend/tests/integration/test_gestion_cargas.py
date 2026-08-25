"""Alta y edición de cargas por Operaciones.

Es lo que en el sistema viejo se hacía creando un `Warehouse`. Lo que estas
pruebas vigilan es la frontera con el motor de transiciones: acá se corrigen
datos, nunca se mueve el estado.
"""

import uuid

import pytest
from scripts.seed_rbac import sembrar as sembrar_rbac
from scripts.seed_shipment_statuses import sembrar as sembrar_estados
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.rbac.models import RoleCode, ScopeType
from app.modules.rbac.service import obtener_permisos_efectivos
from app.modules.shipments import gestion
from app.modules.shipments.models import ShipmentStatus
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

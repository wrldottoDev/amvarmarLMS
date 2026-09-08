"""Pipeline de escritura con confirmación (ADR-0012, Fase 4):

    pending_action -> preview -> confirmación -> revalidación -> execute

`crear_prealerta_borrador` es la primera herramienta WRITE, y prueba la
infraestructura genérica: el preview (`executors_escritura.py`) solo
persiste una propuesta `PENDING`, nunca toca `shipments`; la confirmación
(`confirmaciones.py`) es la única que crea la carga, y lo hace a través de
`shipments.gestion.crear` — el mismo command que usa `POST /shipments`, no un
`INSERT` propio.
"""

import uuid

import pytest
from scripts.seed_rbac import sembrar as sembrar_rbac
from scripts.seed_shipment_statuses import sembrar as sembrar_estados
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.copilot import confirmaciones, executors_escritura
from app.modules.rbac.models import RoleCode, ScopeType
from app.modules.rbac.service import PermisosEfectivos, obtener_permisos_efectivos
from app.modules.shipments.gestion import DatosInvalidos
from app.modules.shipments.service import SinPermisoParaTransicion

pytestmark = pytest.mark.integration


async def _entorno(session: AsyncSession) -> dict:
    await sembrar_rbac(session)
    await sembrar_estados(session)

    empresa_a = await _empresa(session, f"Escritura A {uuid.uuid4().hex[:6]} S.A.")
    empresa_b = await _empresa(session, f"Escritura B {uuid.uuid4().hex[:6]} S.A.")
    cliente_a = await _usuario_con_rol(
        session, RoleCode.CLIENT_ADMIN, ScopeType.ORGANIZATION, empresa_a
    )
    operaciones = await _usuario_con_rol(session, RoleCode.OPS_ADMIN, ScopeType.GLOBAL, None)
    origen = await _ubicacion(session, "US", "MIA", "Miami")
    destino = await _ubicacion(session, "CR", "SJO", "San José")

    return {
        "empresa_a": empresa_a,
        "empresa_b": empresa_b,
        "cliente_a": cliente_a,
        "operaciones": operaciones,
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
            {"e": f"esc-{uuid.uuid4().hex[:10]}@amvarmar.com"},
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


async def _permisos(session: AsyncSession, redis, user_id: uuid.UUID) -> PermisosEfectivos:
    return await obtener_permisos_efectivos(session, redis, user_id)


async def _propuesta(session: AsyncSession, propuesta_id: uuid.UUID):
    return (
        await session.execute(
            text("SELECT status, payload, expires_at FROM copilot_action_proposals WHERE id = :id"),
            {"id": propuesta_id},
        )
    ).one()


ARGUMENTOS_COMPLETOS = {
    "descripcion": "Repuestos varios",
    "origen_location_code": "US-MIA",
    "destino_location_code": "CR-SJO",
    "factura": "INV-0001",
    "peso_kg": 42.5,
    "bulto_tipo": "BOX",
    "bulto_cantidad": 2,
}


class TestPreview:
    async def test_persiste_una_propuesta_pending_con_los_datos_resueltos(
        self, session: AsyncSession, redis
    ) -> None:
        ctx = await _entorno(session)
        permisos = await _permisos(session, redis, ctx["cliente_a"])

        resultado = await executors_escritura.crear_prealerta_borrador(
            session, permisos, ctx["cliente_a"], ctx["empresa_a"], ARGUMENTOS_COMPLETOS
        )

        assert resultado["advertencias"] == []
        assert resultado["action_code"] == "crear_prealerta_borrador"
        fila = await _propuesta(session, uuid.UUID(resultado["id"]))
        assert fila.status == "PENDING"
        assert fila.payload["origen_location_id"] == str(ctx["origen"])
        assert fila.payload["destino_location_id"] == str(ctx["destino"])
        assert fila.payload["factura"] == "INV-0001"

    async def test_ningun_campo_expone_id_interno_de_otra_empresa(
        self, session: AsyncSession, redis
    ) -> None:
        """Forma de salida: la persona ve el NOMBRE del lugar, no el UUID."""
        ctx = await _entorno(session)
        permisos = await _permisos(session, redis, ctx["cliente_a"])

        resultado = await executors_escritura.crear_prealerta_borrador(
            session, permisos, ctx["cliente_a"], ctx["empresa_a"], ARGUMENTOS_COMPLETOS
        )

        valores = {c["nombre"]: c["valor"] for c in resultado["campos"]}
        assert valores["origen_location_code"] == "Miami"
        assert str(ctx["origen"]) not in str(resultado)

    async def test_codigo_de_ubicacion_inexistente_no_bloquea_pero_advierte(
        self, session: AsyncSession, redis
    ) -> None:
        ctx = await _entorno(session)
        permisos = await _permisos(session, redis, ctx["cliente_a"])
        argumentos = {**ARGUMENTOS_COMPLETOS, "origen_location_code": "ZZ-NOPE"}

        resultado = await executors_escritura.crear_prealerta_borrador(
            session, permisos, ctx["cliente_a"], ctx["empresa_a"], argumentos
        )

        assert any("ZZ-NOPE" in a for a in resultado["advertencias"])
        fila = await _propuesta(session, uuid.UUID(resultado["id"]))
        assert fila.payload["origen_location_id"] is None

    async def test_sin_bultos_advierte_pero_igual_persiste_el_borrador(
        self, session: AsyncSession, redis
    ) -> None:
        ctx = await _entorno(session)
        permisos = await _permisos(session, redis, ctx["cliente_a"])
        argumentos = {**ARGUMENTOS_COMPLETOS, "bulto_tipo": None, "bulto_cantidad": None}

        resultado = await executors_escritura.crear_prealerta_borrador(
            session, permisos, ctx["cliente_a"], ctx["empresa_a"], argumentos
        )

        assert any("pieza" in a for a in resultado["advertencias"])
        fila = await _propuesta(session, uuid.UUID(resultado["id"]))
        assert fila.status == "PENDING"

    async def test_personal_interno_sin_empresa_no_puede_pedir_el_borrador(
        self, session: AsyncSession, redis
    ) -> None:
        ctx = await _entorno(session)
        permisos = await _permisos(session, redis, ctx["operaciones"])

        resultado = await executors_escritura.crear_prealerta_borrador(
            session, permisos, ctx["operaciones"], None, ARGUMENTOS_COMPLETOS
        )

        assert "error" in resultado
        total = (
            await session.execute(text("SELECT count(*) FROM copilot_action_proposals"))
        ).scalar_one()
        assert total == 0


class TestConfirmacion:
    async def _crear_borrador(self, session, redis, ctx, argumentos=None):
        permisos = await _permisos(session, redis, ctx["cliente_a"])
        resultado = await executors_escritura.crear_prealerta_borrador(
            session,
            permisos,
            ctx["cliente_a"],
            ctx["empresa_a"],
            argumentos or ARGUMENTOS_COMPLETOS,
        )
        return uuid.UUID(resultado["id"])

    async def test_confirmar_crea_la_carga_con_gestion_crear(
        self, session: AsyncSession, redis
    ) -> None:
        ctx = await _entorno(session)
        propuesta_id = await self._crear_borrador(session, redis, ctx)
        permisos = await _permisos(session, redis, ctx["cliente_a"])

        resultado = await confirmaciones.confirmar_crear_prealerta_borrador(
            session, permisos, propuesta_id, {}
        )

        fila = (
            await session.execute(
                text(
                    "SELECT company_id, current_status_code, description FROM shipments WHERE id = :id"
                ),
                {"id": uuid.UUID(resultado["shipment_id"])},
            )
        ).one()
        assert fila.company_id == ctx["empresa_a"]
        assert fila.current_status_code == "PRE_ALERT"
        assert fila.description == "Repuestos varios"
        assert resultado["shipment_number"].startswith("SHP-")

    async def test_campos_de_la_persona_pisan_el_borrador(
        self, session: AsyncSession, redis
    ) -> None:
        ctx = await _entorno(session)
        propuesta_id = await self._crear_borrador(session, redis, ctx)
        permisos = await _permisos(session, redis, ctx["cliente_a"])

        resultado = await confirmaciones.confirmar_crear_prealerta_borrador(
            session, permisos, propuesta_id, {"descripcion": "Corregido por la persona"}
        )

        descripcion = (
            await session.execute(
                text("SELECT description FROM shipments WHERE id = :id"),
                {"id": uuid.UUID(resultado["shipment_id"])},
            )
        ).scalar_one()
        assert descripcion == "Corregido por la persona"

    async def test_campos_no_pueden_cambiar_la_empresa_de_la_propuesta(
        self, session: AsyncSession, redis
    ) -> None:
        """Aislamiento multiempresa: `company_id` sale SIEMPRE de la propuesta
        ya persistida (que a su vez salió del JWT al crearla), nunca de lo que
        la persona mande en `campos`."""
        ctx = await _entorno(session)
        propuesta_id = await self._crear_borrador(session, redis, ctx)
        permisos = await _permisos(session, redis, ctx["cliente_a"])

        resultado = await confirmaciones.confirmar_crear_prealerta_borrador(
            session, permisos, propuesta_id, {"company_id": str(ctx["empresa_b"])}
        )

        company_id = (
            await session.execute(
                text("SELECT company_id FROM shipments WHERE id = :id"),
                {"id": uuid.UUID(resultado["shipment_id"])},
            )
        ).scalar_one()
        assert company_id == ctx["empresa_a"]

    async def test_sin_bultos_falla_controlado_sin_crear_nada(
        self, session: AsyncSession, redis
    ) -> None:
        ctx = await _entorno(session)
        propuesta_id = await self._crear_borrador(
            session,
            redis,
            ctx,
            {**ARGUMENTOS_COMPLETOS, "bulto_tipo": None, "bulto_cantidad": None},
        )
        permisos = await _permisos(session, redis, ctx["cliente_a"])

        with pytest.raises(DatosInvalidos):
            await confirmaciones.confirmar_crear_prealerta_borrador(
                session, permisos, propuesta_id, {}
            )

        total = (await session.execute(text("SELECT count(*) FROM shipments"))).scalar_one()
        assert total == 0

    async def test_corrige_el_origen_por_codigo_cuando_no_se_resolvio_al_proponer(
        self, session: AsyncSession, redis
    ) -> None:
        """El campo que la persona ve y corrige es el CÓDIGO
        (`origen_location_code`), no el ID que guarda el borrador — sin
        traducir uno al otro, corregir un origen que AMVI no encontró al
        proponer no tendría ningún efecto real."""
        ctx = await _entorno(session)
        propuesta_id = await self._crear_borrador(
            session, redis, ctx, {**ARGUMENTOS_COMPLETOS, "origen_location_code": "ZZ-NOPE"}
        )
        permisos = await _permisos(session, redis, ctx["cliente_a"])

        resultado = await confirmaciones.confirmar_crear_prealerta_borrador(
            session, permisos, propuesta_id, {"origen_location_code": "US-MIA"}
        )

        origen_id = (
            await session.execute(
                text("SELECT origin_location_id FROM shipments WHERE id = :id"),
                {"id": uuid.UUID(resultado["shipment_id"])},
            )
        ).scalar_one()
        assert origen_id == ctx["origen"]

    async def test_corregir_con_un_codigo_de_ubicacion_inexistente_falla_controlado(
        self, session: AsyncSession, redis
    ) -> None:
        ctx = await _entorno(session)
        propuesta_id = await self._crear_borrador(session, redis, ctx)
        permisos = await _permisos(session, redis, ctx["cliente_a"])

        with pytest.raises(DatosInvalidos):
            await confirmaciones.confirmar_crear_prealerta_borrador(
                session, permisos, propuesta_id, {"origen_location_code": "ZZ-NOPE"}
            )

        total = (await session.execute(text("SELECT count(*) FROM shipments"))).scalar_one()
        assert total == 0

    async def test_revalida_el_permiso_al_confirmar_no_solo_al_proponer(
        self, session: AsyncSession, redis
    ) -> None:
        """El borrador se preparó con permiso; si el actor lo perdió antes de
        confirmar, `gestion.crear` (revalidación real) lo rechaza."""
        ctx = await _entorno(session)
        propuesta_id = await self._crear_borrador(session, redis, ctx)

        # Permisos vacíos: simula que el rol cambió entre proponer y confirmar.
        permisos_sin_nada = PermisosEfectivos(
            user_id=ctx["cliente_a"], authz_version=1, permisos=()
        )

        with pytest.raises(SinPermisoParaTransicion):
            await confirmaciones.confirmar_crear_prealerta_borrador(
                session, permisos_sin_nada, propuesta_id, {}
            )

        total = (await session.execute(text("SELECT count(*) FROM shipments"))).scalar_one()
        assert total == 0

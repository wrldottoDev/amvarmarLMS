"""`procesar_factura_ocr`: lee una factura ya subida (y ya atada a una carga
existente) y arma un borrador para corregir esa carga (ADR-0012, Fase 6).

A diferencia de `crear_prealerta_borrador` (Fase 4), acá nunca hay que crear
nada: todo documento se sube ya atado a una carga
(`documents.service.preparar_subida` exige `shipment_id`), así que confirmar
esta acción siempre corrige una carga que ya existía.
"""

import uuid
from typing import Any

import pytest
from scripts.seed_document_types import sembrar as sembrar_documentos
from scripts.seed_rbac import sembrar as sembrar_rbac
from scripts.seed_shipment_statuses import sembrar as sembrar_estados
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.storage import s3
from app.modules.copilot import confirmaciones, executors_escritura
from app.modules.copilot.provider import DescripcionFactura, proveedor_actual
from app.modules.documents import service as documents_service
from app.modules.rbac.catalog import Perm
from app.modules.rbac.models import RoleCode, ScopeType
from app.modules.rbac.service import PermisoEfectivo, PermisosEfectivos, obtener_permisos_efectivos
from app.modules.shipments.gestion import DatosInvalidos
from app.modules.shipments.service import SinPermisoParaTransicion
from tests.piezas import sembrar_pieza

pytestmark = pytest.mark.integration


class _ProveedorFacturaFalso:
    """Solo implementa lo que `procesar_factura_ocr` necesita — nunca se
    llama `responder()` en estas pruebas, así que ni hace falta fingirlo."""

    def __init__(self, descripcion: DescripcionFactura) -> None:
        self._descripcion = descripcion

    async def describir_factura(
        self, *, media_type: str, contenido_base64: str
    ) -> DescripcionFactura:
        return self._descripcion


def _descripcion(**overrides: Any) -> DescripcionFactura:
    base: dict[str, Any] = {
        "numero_guia": "INV-9999",
        "proveedor": "Acme Trading",
        "monto": 500.0,
        "moneda": "USD",
        "cliente": "Importaciones Alfa",
    }
    base.update(overrides)
    return DescripcionFactura(**base)


async def _empresa(session: AsyncSession) -> uuid.UUID:
    return (
        await session.execute(
            text("INSERT INTO companies (legal_name, status) VALUES (:n,'ACTIVE') RETURNING id"),
            {"n": f"OCR {uuid.uuid4().hex[:6]} S.A."},
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
            {"e": f"ocr-{uuid.uuid4().hex[:10]}@amvarmar.com"},
        )
    ).scalar_one()
    await session.execute(
        text("""
            INSERT INTO user_role_assignments (user_id, role_id, scope_type, company_id)
            SELECT :u, r.id, :scope, :c FROM roles r WHERE r.code = :rol
        """),
        {"u": user_id, "rol": rol, "scope": scope, "c": empresa},
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


async def _entorno(session: AsyncSession) -> dict[str, Any]:
    await sembrar_rbac(session)
    await sembrar_estados(session)
    await sembrar_documentos(session)
    await documents_service.sembrar_limites(session)

    empresa = await _empresa(session)
    operaciones = await _usuario_con_rol(session, RoleCode.OPS_ADMIN, ScopeType.GLOBAL, None)
    origen = await _ubicacion(session, "US", "MIA", "Miami")
    destino = await _ubicacion(session, "CR", "SJO", "San José")

    shipment = (
        await session.execute(
            text("""
                INSERT INTO shipments
                    (company_id, created_by, current_status_code,
                     origin_location_id, destination_location_id)
                VALUES (:c,:u,'IN_TRANSIT',:o,:d)
                RETURNING id, row_version, shipment_number
            """),
            {"c": empresa, "u": operaciones, "o": origen, "d": destino},
        )
    ).one()
    await sembrar_pieza(session, shipment.id)

    tipo_factura = (
        await session.execute(
            text("SELECT id FROM document_types WHERE code = 'COMMERCIAL_INVOICE'")
        )
    ).scalar_one()

    return {
        "empresa": empresa,
        "operaciones": operaciones,
        "shipment_id": shipment.id,
        "row_version": shipment.row_version,
        "shipment_number": shipment.shipment_number,
        "tipo_factura": tipo_factura,
    }


async def _documento_listo(
    session: AsyncSession,
    ctx: dict[str, Any],
    bucket: str,
    *,
    contenido: bytes = b"%PDF-1.4\n%%EOF",
) -> uuid.UUID:
    permisos = PermisosEfectivos(
        user_id=ctx["operaciones"],
        authz_version=0,
        permisos=(
            PermisoEfectivo(
                code=Perm.DOCUMENTS_UPLOAD_INTERNAL,
                scope_type=ScopeType.ORGANIZATION,
                company_id=ctx["empresa"],
            ),
        ),
    )
    subida = await documents_service.preparar_subida(
        session,
        shipment_id=ctx["shipment_id"],
        document_type_id=ctx["tipo_factura"],
        issued_by="PROVIDER",
        original_name="factura.pdf",
        company_id=ctx["empresa"],
        actor_user_id=ctx["operaciones"],
        permisos=permisos,
    )
    s3._cliente().put_object(Bucket=bucket, Key=subida.storage_key, Body=contenido)
    await documents_service.completar(
        session, document_id=subida.document_id, company_id=ctx["empresa"]
    )
    return subida.document_id


async def _permisos_reales(
    session: AsyncSession, redis: Any, user_id: uuid.UUID
) -> PermisosEfectivos:
    return await obtener_permisos_efectivos(session, redis, user_id)


class TestPreview:
    async def test_lee_la_factura_y_arma_la_propuesta_con_los_datos_reales(
        self, session: AsyncSession, redis: Any, storage_de_prueba: str
    ) -> None:
        ctx = await _entorno(session)
        document_id = await _documento_listo(session, ctx, storage_de_prueba)
        permisos = await _permisos_reales(session, redis, ctx["operaciones"])
        token = proveedor_actual.set(_ProveedorFacturaFalso(_descripcion()))

        try:
            resultado = await executors_escritura.procesar_factura_ocr(
                session, permisos, ctx["operaciones"], None, {"document_id": str(document_id)}
            )
        finally:
            proveedor_actual.reset(token)

        assert resultado["advertencias"] == []
        assert resultado["action_code"] == "procesar_factura_ocr"
        valores = {c["nombre"]: c["valor"] for c in resultado["campos"]}
        assert valores["factura"] == "INV-9999"
        assert valores["carga"] == ctx["shipment_number"]

        fila = (
            await session.execute(
                text("SELECT status, payload FROM copilot_action_proposals WHERE id = :id"),
                {"id": uuid.UUID(resultado["id"])},
            )
        ).one()
        assert fila.status == "PENDING"
        assert fila.payload["shipment_id"] == str(ctx["shipment_id"])
        assert fila.payload["factura"] == "INV-9999"

    async def test_documento_inexistente_da_error_controlado(
        self, session: AsyncSession, redis: Any
    ) -> None:
        ctx = await _entorno(session)
        permisos = await _permisos_reales(session, redis, ctx["operaciones"])
        token = proveedor_actual.set(_ProveedorFacturaFalso(_descripcion()))

        try:
            resultado = await executors_escritura.procesar_factura_ocr(
                session, permisos, ctx["operaciones"], None, {"document_id": str(uuid.uuid4())}
            )
        finally:
            proveedor_actual.reset(token)

        assert "error" in resultado
        total = (
            await session.execute(text("SELECT count(*) FROM copilot_action_proposals"))
        ).scalar_one()
        assert total == 0

    async def test_sin_permiso_sobre_la_empresa_del_documento_da_error_controlado(
        self, session: AsyncSession, storage_de_prueba: str
    ) -> None:
        ctx = await _entorno(session)
        document_id = await _documento_listo(session, ctx, storage_de_prueba)
        # Permisos vacíos: simula un actor sin alcance sobre esa empresa.
        permisos_sin_nada = PermisosEfectivos(
            user_id=ctx["operaciones"], authz_version=1, permisos=()
        )
        token = proveedor_actual.set(_ProveedorFacturaFalso(_descripcion()))

        try:
            resultado = await executors_escritura.procesar_factura_ocr(
                session,
                permisos_sin_nada,
                ctx["operaciones"],
                None,
                {"document_id": str(document_id)},
            )
        finally:
            proveedor_actual.reset(token)

        assert "error" in resultado

    async def test_documento_ajeno_e_inexistente_dan_el_mismo_mensaje(
        self, session: AsyncSession, redis: Any, storage_de_prueba: str
    ) -> None:
        """No debe poder distinguirse, por el mensaje, si un `document_id` de
        otra empresa existe — sería un oráculo de existencia cross-empresa."""
        ctx = await _entorno(session)
        ajeno = await _documento_listo(session, ctx, storage_de_prueba)
        inexistente = uuid.uuid4()
        permisos_sin_nada = PermisosEfectivos(
            user_id=ctx["operaciones"], authz_version=1, permisos=()
        )
        token = proveedor_actual.set(_ProveedorFacturaFalso(_descripcion()))

        try:
            resultado_ajeno = await executors_escritura.procesar_factura_ocr(
                session, permisos_sin_nada, ctx["operaciones"], None, {"document_id": str(ajeno)}
            )
            resultado_inexistente = await executors_escritura.procesar_factura_ocr(
                session,
                permisos_sin_nada,
                ctx["operaciones"],
                None,
                {"document_id": str(inexistente)},
            )
        finally:
            proveedor_actual.reset(token)

        assert resultado_ajeno == resultado_inexistente

    async def test_documento_todavia_procesando_da_error_controlado(
        self, session: AsyncSession, redis: Any, storage_de_prueba: str
    ) -> None:
        ctx = await _entorno(session)
        document_id = await _documento_listo(session, ctx, storage_de_prueba)
        await session.execute(
            text("UPDATE documents SET upload_status = 'PROCESSING' WHERE id = :id"),
            {"id": document_id},
        )
        permisos = await _permisos_reales(session, redis, ctx["operaciones"])
        token = proveedor_actual.set(_ProveedorFacturaFalso(_descripcion()))

        try:
            resultado = await executors_escritura.procesar_factura_ocr(
                session, permisos, ctx["operaciones"], None, {"document_id": str(document_id)}
            )
        finally:
            proveedor_actual.reset(token)

        assert "error" in resultado

    async def test_campos_no_leidos_generan_advertencias_pero_igual_persiste(
        self, session: AsyncSession, redis: Any, storage_de_prueba: str
    ) -> None:
        ctx = await _entorno(session)
        document_id = await _documento_listo(session, ctx, storage_de_prueba)
        permisos = await _permisos_reales(session, redis, ctx["operaciones"])
        vacia = _descripcion(numero_guia=None, proveedor=None, monto=None, cliente=None)
        token = proveedor_actual.set(_ProveedorFacturaFalso(vacia))

        try:
            resultado = await executors_escritura.procesar_factura_ocr(
                session, permisos, ctx["operaciones"], None, {"document_id": str(document_id)}
            )
        finally:
            proveedor_actual.reset(token)

        assert len(resultado["advertencias"]) == 4
        fila = (
            await session.execute(
                text("SELECT status FROM copilot_action_proposals WHERE id = :id"),
                {"id": uuid.UUID(resultado["id"])},
            )
        ).scalar_one()
        assert fila == "PENDING"


class TestConfirmacion:
    async def _crear_propuesta(
        self, session: AsyncSession, redis: Any, ctx: dict[str, Any], bucket: str, **overrides: Any
    ) -> uuid.UUID:
        document_id = await _documento_listo(session, ctx, bucket)
        permisos = await _permisos_reales(session, redis, ctx["operaciones"])
        token = proveedor_actual.set(_ProveedorFacturaFalso(_descripcion(**overrides)))
        try:
            resultado = await executors_escritura.procesar_factura_ocr(
                session, permisos, ctx["operaciones"], None, {"document_id": str(document_id)}
            )
        finally:
            proveedor_actual.reset(token)
        return uuid.UUID(resultado["id"])

    async def test_confirmar_actualiza_la_factura_de_la_carga_existente(
        self, session: AsyncSession, redis: Any, storage_de_prueba: str
    ) -> None:
        ctx = await _entorno(session)
        propuesta_id = await self._crear_propuesta(session, redis, ctx, storage_de_prueba)
        permisos = await _permisos_reales(session, redis, ctx["operaciones"])

        resultado = await confirmaciones.confirmar_procesar_factura_ocr(
            session, permisos, propuesta_id, {}
        )

        assert resultado["shipment_id"] == str(ctx["shipment_id"])
        valor = (
            await session.execute(
                text(
                    "SELECT value FROM shipment_references "
                    "WHERE shipment_id = :id AND reference_type = 'INVOICE'"
                ),
                {"id": ctx["shipment_id"]},
            )
        ).scalar_one()
        assert valor == "INV-9999"

    async def test_la_persona_puede_corregir_el_numero_de_factura_antes_de_confirmar(
        self, session: AsyncSession, redis: Any, storage_de_prueba: str
    ) -> None:
        ctx = await _entorno(session)
        propuesta_id = await self._crear_propuesta(session, redis, ctx, storage_de_prueba)
        permisos = await _permisos_reales(session, redis, ctx["operaciones"])

        await confirmaciones.confirmar_procesar_factura_ocr(
            session, permisos, propuesta_id, {"factura": "CORREGIDA-1"}
        )

        valor = (
            await session.execute(
                text(
                    "SELECT value FROM shipment_references "
                    "WHERE shipment_id = :id AND reference_type = 'INVOICE'"
                ),
                {"id": ctx["shipment_id"]},
            )
        ).scalar_one()
        assert valor == "CORREGIDA-1"

    async def test_campos_no_pueden_cambiar_la_carga_ni_la_version(
        self, session: AsyncSession, redis: Any, storage_de_prueba: str
    ) -> None:
        ctx = await _entorno(session)
        propuesta_id = await self._crear_propuesta(session, redis, ctx, storage_de_prueba)
        permisos = await _permisos_reales(session, redis, ctx["operaciones"])

        resultado = await confirmaciones.confirmar_procesar_factura_ocr(
            session,
            permisos,
            propuesta_id,
            {"shipment_id": str(uuid.uuid4()), "row_version": 999},
        )

        assert resultado["shipment_id"] == str(ctx["shipment_id"])

    async def test_sin_numero_de_factura_falla_controlado_sin_tocar_nada(
        self, session: AsyncSession, redis: Any, storage_de_prueba: str
    ) -> None:
        ctx = await _entorno(session)
        propuesta_id = await self._crear_propuesta(
            session, redis, ctx, storage_de_prueba, numero_guia=None
        )
        permisos = await _permisos_reales(session, redis, ctx["operaciones"])

        with pytest.raises(DatosInvalidos):
            await confirmaciones.confirmar_procesar_factura_ocr(session, permisos, propuesta_id, {})

        total = (
            await session.execute(text("SELECT count(*) FROM shipment_references"))
        ).scalar_one()
        assert total == 0

    async def test_revalida_el_permiso_al_confirmar_no_solo_al_proponer(
        self, session: AsyncSession, redis: Any, storage_de_prueba: str
    ) -> None:
        ctx = await _entorno(session)
        propuesta_id = await self._crear_propuesta(session, redis, ctx, storage_de_prueba)
        permisos_sin_nada = PermisosEfectivos(
            user_id=ctx["operaciones"], authz_version=1, permisos=()
        )

        with pytest.raises(SinPermisoParaTransicion):
            await confirmaciones.confirmar_procesar_factura_ocr(
                session, permisos_sin_nada, propuesta_id, {}
            )

        total = (
            await session.execute(text("SELECT count(*) FROM shipment_references"))
        ).scalar_one()
        assert total == 0

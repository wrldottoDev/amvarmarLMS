"""Ciclo de tool calling (ADR-0012).

Ninguna prueba de este archivo llama a la API real: `ProveedorFalso` implementa
el mismo `Protocol` que `ProveedorOpenAI` (`provider.ProveedorIA`), así que
`procesar_turno` no puede distinguirlos.
"""

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.modules.copilot import service as copilot_service
from app.modules.copilot.provider import (
    LlamadaHerramienta,
    ProveedorNoDisponible,
    RespuestaProveedor,
)
from app.modules.rbac.catalog import Perm
from app.modules.rbac.service import PermisoEfectivo, PermisosEfectivos

pytestmark = pytest.mark.integration


def _permisos(*codigos: str) -> PermisosEfectivos:
    return PermisosEfectivos(
        user_id=uuid4(),
        authz_version=1,
        permisos=tuple(
            PermisoEfectivo(code=c, scope_type="GLOBAL", company_id=None) for c in codigos
        ),
    )


async def _empresa(session: AsyncSession) -> UUID:
    return (
        await session.execute(
            text("INSERT INTO companies (legal_name, status) VALUES (:n,'ACTIVE') RETURNING id"),
            {"n": f"Empresa {uuid4().hex[:6]} S.A."},
        )
    ).scalar_one()


async def _usuario(session: AsyncSession) -> UUID:
    """`audit_logs.actor_user_id` tiene FK a `users`: el registro de auditoría
    del ciclo de tool calling exige un usuario real, no cualquier UUID."""
    return (
        await session.execute(
            text("""
                INSERT INTO users (email, password_hash, first_name, last_name, status)
                VALUES (:email, 'h', 'Ana', 'Pérez', 'ACTIVE') RETURNING id
            """),
            {"email": f"copilot-{uuid4().hex[:8]}@pruebas.amvarmar.com"},
        )
    ).scalar_one()


@dataclass
class ProveedorFalso:
    """Cola de respuestas prefabricadas. Cada llamada a `responder()` consume
    la siguiente y guarda el `entrada` recibido, para poder inspeccionar qué
    le llegó al "modelo" en cada vuelta."""

    guion: list[RespuestaProveedor]
    llamadas: list[list[dict[str, Any]]] = field(default_factory=list)

    async def responder(
        self, *, entrada: list[dict[str, Any]], herramientas: list[dict[str, Any]]
    ) -> RespuestaProveedor:
        self.llamadas.append(entrada)
        if len(self.llamadas) > len(self.guion):
            raise AssertionError("ProveedorFalso: se pidió una respuesta de más del guion.")
        return self.guion[len(self.llamadas) - 1]


class ProveedorCaido:
    async def responder(
        self, *, entrada: list[dict[str, Any]], herramientas: list[dict[str, Any]]
    ) -> RespuestaProveedor:
        raise ProveedorNoDisponible("simulado para pruebas")


@dataclass
class ProveedorInsistente:
    """Nunca devuelve texto final: siempre otra llamada a herramienta. Para
    probar el tope de vueltas por turno."""

    nombre_herramienta: str
    llamadas: int = 0

    async def responder(
        self, *, entrada: list[dict[str, Any]], herramientas: list[dict[str, Any]]
    ) -> RespuestaProveedor:
        self.llamadas += 1
        call_id = f"call_{self.llamadas}"
        return RespuestaProveedor(
            id=f"resp_{self.llamadas}",
            texto=None,
            llamadas_herramientas=(
                LlamadaHerramienta(
                    call_id=call_id, nombre=self.nombre_herramienta, argumentos_json="{}"
                ),
            ),
            items_salida=(
                {
                    "type": "function_call",
                    "call_id": call_id,
                    "name": self.nombre_herramienta,
                    "arguments": "{}",
                },
            ),
            tokens_entrada=1,
            tokens_salida=1,
        )


def _texto(id_: str, texto: str) -> RespuestaProveedor:
    return RespuestaProveedor(
        id=id_,
        texto=texto,
        llamadas_herramientas=(),
        items_salida=(),
        tokens_entrada=5,
        tokens_salida=5,
    )


def _llamada_herramienta(
    id_: str, nombre: str, argumentos_json: str, call_id: str = "call_1"
) -> RespuestaProveedor:
    return RespuestaProveedor(
        id=id_,
        texto=None,
        llamadas_herramientas=(
            LlamadaHerramienta(call_id=call_id, nombre=nombre, argumentos_json=argumentos_json),
        ),
        items_salida=(
            {
                "type": "function_call",
                "call_id": call_id,
                "name": nombre,
                "arguments": argumentos_json,
            },
        ),
        tokens_entrada=5,
        tokens_salida=5,
    )


async def _correr(
    session: AsyncSession,
    proveedor: Any,
    permisos: PermisosEfectivos,
    actor_user_id: UUID,
    mensaje: str = "hola",
    company_id: UUID | None = None,
) -> list[copilot_service.EventoSSE]:
    eventos = []
    async for evento in copilot_service.procesar_turno(
        session,
        proveedor=proveedor,
        actor_user_id=actor_user_id,
        company_id=company_id,
        permisos=permisos,
        nombre_actor="Ana Pérez",
        es_cliente=False,
        empresa_nombre=None,
        mensajes=[{"rol": "user", "contenido": mensaje}],
    ):
        eventos.append(evento)
    return eventos


class TestRespuestaSinHerramientas:
    async def test_una_respuesta_de_texto_no_llama_ninguna_herramienta(
        self, session: AsyncSession
    ) -> None:
        actor = await _usuario(session)
        proveedor = ProveedorFalso(guion=[_texto("resp_1", "Todo en orden.")])

        eventos = await _correr(session, proveedor, _permisos(), actor)

        tipos = [e.evento for e in eventos]
        assert "token" in tipos
        assert "fin" in tipos
        assert len(proveedor.llamadas) == 1


class TestRevalidacionAlEjecutar:
    """El control que realmente protege — no importa qué se le ofreció al
    modelo, esto es lo que corre de verdad cuando pide algo."""

    async def test_herramienta_inventada_no_crashea_y_queda_auditada(
        self, session: AsyncSession
    ) -> None:
        actor = await _usuario(session)
        proveedor = ProveedorFalso(
            guion=[
                _llamada_herramienta("resp_1", "borrar_todas_las_cargas", "{}"),
                _texto("resp_2", "No puedo hacer eso."),
            ]
        )

        await _correr(session, proveedor, _permisos(Perm.SHIPMENTS_READ), actor)

        assert len(proveedor.llamadas) == 2
        # La segunda llamada al proveedor incluye el resultado de la primera:
        # el "modelo" ve que la herramienta inventada no existe.
        salida_de_herramienta = next(
            item for item in proveedor.llamadas[1] if item.get("type") == "function_call_output"
        )
        assert "no existe" in salida_de_herramienta["output"]

        fila = (
            await session.execute(
                text(
                    "SELECT outcome, after_data FROM audit_logs "
                    "WHERE action = 'copilot.tool.invoked' AND actor_user_id = :actor "
                    "ORDER BY occurred_at DESC LIMIT 1"
                ),
                {"actor": actor},
            )
        ).one()
        assert fila.outcome == "FAILED"
        assert fila.after_data["motivo"] == "inexistente"

    async def test_herramienta_no_ofrecida_se_rechaza_en_la_revalidacion(
        self, session: AsyncSession
    ) -> None:
        """El modelo pide algo que el filtro de permisos nunca le habría
        ofrecido — simula una alucinación de capacidad."""
        actor = await _usuario(session)
        proveedor = ProveedorFalso(
            guion=[
                _llamada_herramienta("resp_1", "procesar_factura_ocr", '{"document_id": "x"}'),
                _texto("resp_2", "No tenés permiso."),
            ]
        )

        # Permisos de un cliente: sin `documents.upload.internal`.
        await _correr(session, proveedor, _permisos(Perm.SHIPMENTS_READ), actor)

        fila = (
            await session.execute(
                text(
                    "SELECT outcome, after_data FROM audit_logs "
                    "WHERE action = 'copilot.tool.invoked' AND actor_user_id = :actor "
                    "ORDER BY occurred_at DESC LIMIT 1"
                ),
                {"actor": actor},
            )
        ).one()
        assert fila.outcome == "DENIED"
        assert fila.after_data["motivo"] == "sin_permiso"

    async def test_argumentos_invalidos_se_rechazan(self, session: AsyncSession) -> None:
        actor = await _usuario(session)
        proveedor = ProveedorFalso(
            guion=[
                # Falta el campo requerido `shipment_number`.
                _llamada_herramienta("resp_1", "consultar_estado_carga", "{}"),
                _texto("resp_2", "Argumentos inválidos."),
            ]
        )

        await _correr(session, proveedor, _permisos(Perm.SHIPMENTS_READ), actor)

        fila = (
            await session.execute(
                text(
                    "SELECT after_data FROM audit_logs "
                    "WHERE action = 'copilot.tool.invoked' AND actor_user_id = :actor "
                    "ORDER BY occurred_at DESC LIMIT 1"
                ),
                {"actor": actor},
            )
        ).one()
        assert fila.after_data["motivo"] == "argumentos_invalidos"

    async def test_herramienta_permitida_sin_ejecutor_responde_controlado(
        self, session: AsyncSession
    ) -> None:
        """Fase 3 conectó las de LECTURA y Fase 4 conectó la primera de
        ESCRITURA (`crear_prealerta_borrador`); `procesar_factura_ocr` sigue
        sin ejecutor. Debe fallar limpio, nunca inventar datos."""
        actor = await _usuario(session)
        proveedor = ProveedorFalso(
            guion=[
                _llamada_herramienta("resp_1", "procesar_factura_ocr", '{"document_id": "x"}'),
                _texto("resp_2", "No puedo hacer eso todavía."),
            ]
        )

        eventos = await _correr(
            session, proveedor, _permisos(Perm.DOCUMENTS_UPLOAD_INTERNAL), actor
        )

        assert any(e.evento == "token" for e in eventos)
        salida = next(
            item for item in proveedor.llamadas[1] if item.get("type") == "function_call_output"
        )
        assert "no está disponible" in salida["output"]


class TestEventoDePropuesta:
    """El frontend dibuja la tarjeta editable a partir de un evento SSE
    dedicado, no de lo que el modelo narre en texto — sin esto, la única
    forma de que la interfaz supiera el `id` de la propuesta sería parsear la
    respuesta del modelo."""

    async def test_una_escritura_exitosa_emite_el_evento_propuesta(
        self, session: AsyncSession
    ) -> None:
        actor = await _usuario(session)
        empresa = await _empresa(session)
        proveedor = ProveedorFalso(
            guion=[
                _llamada_herramienta(
                    "resp_1", "crear_prealerta_borrador", '{"descripcion": "Repuestos"}'
                ),
                _texto("resp_2", "Preparé el borrador."),
            ]
        )

        eventos = await _correr(
            session,
            proveedor,
            _permisos(Perm.COPILOT_TOOLS_DRAFT, Perm.SHIPMENTS_CREATE),
            actor,
            company_id=empresa,
        )

        propuesta = next(e for e in eventos if e.evento == "propuesta")
        assert propuesta.datos["action_code"] == "crear_prealerta_borrador"
        assert "id" in propuesta.datos

    async def test_una_escritura_sin_ejecutor_conectado_no_emite_propuesta(
        self, session: AsyncSession
    ) -> None:
        actor = await _usuario(session)
        proveedor = ProveedorFalso(
            guion=[
                _llamada_herramienta("resp_1", "procesar_factura_ocr", '{"document_id": "x"}'),
                _texto("resp_2", "No puedo hacer eso todavía."),
            ]
        )

        eventos = await _correr(
            session, proveedor, _permisos(Perm.DOCUMENTS_UPLOAD_INTERNAL), actor
        )

        assert not any(e.evento == "propuesta" for e in eventos)

    async def test_una_lectura_nunca_emite_propuesta(self, session: AsyncSession) -> None:
        actor = await _usuario(session)
        proveedor = ProveedorFalso(
            guion=[
                _llamada_herramienta("resp_1", "mis_pendientes", "{}"),
                _texto("resp_2", "No tenés pendientes."),
            ]
        )

        eventos = await _correr(session, proveedor, _permisos(Perm.SHIPMENTS_READ), actor)

        assert not any(e.evento == "propuesta" for e in eventos)


class TestLimites:
    async def test_demasiados_mensajes_se_rechaza_sin_llamar_al_proveedor(
        self, session: AsyncSession
    ) -> None:
        actor = await _usuario(session)
        proveedor = ProveedorFalso(guion=[])
        limite = get_settings().copilot_max_mensajes_por_turno

        eventos = []
        async for evento in copilot_service.procesar_turno(
            session,
            proveedor=proveedor,
            actor_user_id=actor,
            company_id=None,
            permisos=_permisos(),
            nombre_actor="Ana Pérez",
            es_cliente=False,
            empresa_nombre=None,
            mensajes=[{"rol": "user", "contenido": f"mensaje {i}"} for i in range(limite + 1)],
        ):
            eventos.append(evento)

        assert eventos[0].evento == "error"
        assert eventos[0].datos["code"] == "COPILOT_DEMASIADOS_MENSAJES"
        assert proveedor.llamadas == []

    async def test_un_proveedor_que_siempre_pide_herramientas_corta_en_el_tope(
        self, session: AsyncSession
    ) -> None:
        actor = await _usuario(session)
        proveedor = ProveedorInsistente(nombre_herramienta="consultar_estado_carga")

        eventos = await _correr(session, proveedor, _permisos(Perm.SHIPMENTS_READ), actor)

        assert any(
            e.evento == "error" and e.datos["code"] == "COPILOT_LIMITE_HERRAMIENTAS"
            for e in eventos
        )
        # El tope + 1 vueltas, no un bucle infinito.
        assert proveedor.llamadas == get_settings().copilot_max_tool_calls_por_turno + 1


class TestProveedorCaido:
    async def test_no_rompe_el_turno_devuelve_evento_de_error(self, session: AsyncSession) -> None:
        actor = await _usuario(session)

        eventos = await _correr(session, ProveedorCaido(), _permisos(), actor)

        assert len(eventos) == 1
        assert eventos[0].evento == "error"
        assert eventos[0].datos["code"] == "COPILOT_NO_DISPONIBLE"


class TestSinDatosDeConversacionPersistidos:
    async def test_el_texto_de_los_mensajes_no_queda_en_ninguna_columna_de_auditoria(
        self, session: AsyncSession
    ) -> None:
        actor = await _usuario(session)
        marca = f"secreto-{uuid4().hex[:8]}"
        proveedor = ProveedorFalso(guion=[_texto("resp_1", "ok")])

        await _correr(session, proveedor, _permisos(), actor, mensaje=f"mi tarjeta es {marca}")

        fila = (
            await session.execute(
                text("SELECT count(*) FROM audit_logs WHERE after_data::text LIKE :patron"),
                {"patron": f"%{marca}%"},
            )
        ).scalar_one()
        assert fila == 0

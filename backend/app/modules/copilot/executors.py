"""Ejecutores de las herramientas de LECTURA (ADR-0012, Fase 3).

Cada ejecutor recibe la sesión, los permisos YA revalidados, el actor y los
argumentos YA validados contra el modelo Pydantic de la herramienta — y
consulta con el mismo alcance que usaría cualquier otro endpoint. Ninguno
recibe `company_id` del modelo: el alcance sale de `permisos`, resuelto del
JWT antes de llegar acá.

Reusan las consultas existentes (`shipments.queries`, `documents.service`,
`dispatches.queries`, `users.columnas`) en vez de escribir SQL nuevo — dos
caminos hacia el mismo dato solo pueden divergir con el tiempo.

Las de ESCRITURA (Fase 4+) no viven acá: un ejecutor de lectura devuelve datos
directo; una de escritura arma una propuesta `PENDING` y no toca el dominio
hasta que la persona confirma.
"""

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import RecursoNoEncontrado
from app.modules.copilot import base_de_conocimiento
from app.modules.dispatches import queries as dispatches_queries
from app.modules.documents.service import expediente
from app.modules.rbac.service import PermisosEfectivos
from app.modules.shipments.queries import (
    FiltrosListado,
    alcance_de_lectura,
    listar_shipments,
)
from app.modules.users import columnas as columnas_usuario

EjecutorHerramienta = Callable[
    [AsyncSession, PermisosEfectivos, UUID, UUID | None, dict[str, Any]],
    Awaitable[dict[str, Any]],
]

# Tope duro independiente del que pida el modelo: una "búsqueda" que devuelve
# 100 filas no es una respuesta conversacional, es un volcado de tabla.
_LIMITE_BUSQUEDA = 10
_LIMITE_MIS_PENDIENTES = 50
_LIMITE_LISTAR_DESPACHOS = 20


def _resumen_carga(fila: Any) -> dict[str, Any]:
    """Los campos que le sirven al modelo, no `SELECT *`. Sin hashes, sin
    `row_version`, sin nada que no se le vaya a mostrar a la persona igual."""
    return {
        "shipment_number": fila.shipment_number,
        "referencia": fila.wr or fila.factura or fila.shipment_number,
        "empresa": fila.company_name,
        "estado": fila.current_status_code,
        "eta": fila.estimated_arrival_at,
        "shipper": fila.shipper,
        "carrier": fila.carrier,
        "bultos": fila.package_count,
        "peso_kg": fila.weight_kg,
        "requisitos_abiertos": fila.requisitos_abiertos,
        "requisitos_del_cliente": fila.requisitos_del_cliente,
    }


async def _una_carga_por_referencia(
    session: AsyncSession, *, permisos: PermisosEfectivos, referencia: str
) -> Any | None:
    """`consultar_estado_carga` y `explicar_que_falta` reciben un código
    legible (SHP o factura), no un UUID: la misma búsqueda combinada que usa
    el listado (`q`), acotada a la primera coincidencia."""
    pagina = await listar_shipments(
        session,
        permisos=permisos,
        filtros=FiltrosListado(texto=referencia),
        limite=1,
    )
    return pagina.items[0] if pagina.items else None


async def consultar_estado_carga(
    session: AsyncSession,
    permisos: PermisosEfectivos,
    actor_user_id: UUID,
    company_id: UUID | None,
    argumentos: dict[str, Any],
) -> dict[str, Any]:
    fila = await _una_carga_por_referencia(
        session, permisos=permisos, referencia=argumentos["shipment_number"]
    )
    if fila is None:
        return {"encontrada": False}
    return {"encontrada": True, **_resumen_carga(fila)}


async def buscar_cargas(
    session: AsyncSession,
    permisos: PermisosEfectivos,
    actor_user_id: UUID,
    company_id: UUID | None,
    argumentos: dict[str, Any],
) -> dict[str, Any]:
    pagina = await listar_shipments(
        session,
        permisos=permisos,
        filtros=FiltrosListado(texto=argumentos["q"]),
        limite=_LIMITE_BUSQUEDA,
    )
    return {
        "resultados": [_resumen_carga(f) for f in pagina.items],
        "hay_mas": pagina.has_more,
    }


async def explicar_que_falta(
    session: AsyncSession,
    permisos: PermisosEfectivos,
    actor_user_id: UUID,
    company_id: UUID | None,
    argumentos: dict[str, Any],
) -> dict[str, Any]:
    fila = await _una_carga_por_referencia(
        session, permisos=permisos, referencia=argumentos["shipment_number"]
    )
    if fila is None:
        return {"encontrada": False}

    alcance = alcance_de_lectura(permisos)
    exp = await expediente(
        session,
        shipment_id=fila.id,
        company_ids=None if alcance.global_ else alcance.company_ids,
    )
    return {
        "encontrada": True,
        "shipment_number": fila.shipment_number,
        "estado": fila.current_status_code,
        "requisitos_abiertos": [
            {
                "titulo": r.label,
                "estado": r.status,
                "lo_sube": r.required_from,
                "bloquea_despacho": r.blocks_dispatch,
            }
            for r in exp.requisitos
            if r.status not in ("FULFILLED", "VERIFIED", "NOT_APPLICABLE", "WAIVED", "CANCELLED")
        ],
    }


async def mis_pendientes(
    session: AsyncSession,
    permisos: PermisosEfectivos,
    actor_user_id: UUID,
    company_id: UUID | None,
    argumentos: dict[str, Any],
) -> dict[str, Any]:
    alcance = alcance_de_lectura(permisos)
    # Alcance no global (cliente, o interno con permiso solo de su empresa):
    # lo que le toca A ÉL es lo que le falta a su propia empresa. Alcance
    # global: lo que le falta a cualquiera, como el resto de Operaciones ve.
    solo_del_cliente = not alcance.global_

    pagina = await listar_shipments(
        session, permisos=permisos, filtros=FiltrosListado(), limite=_LIMITE_MIS_PENDIENTES
    )

    def _pendiente(fila: Any) -> bool:
        if solo_del_cliente:
            return bool(fila.requisitos_del_cliente > 0)
        return bool(fila.requisitos_abiertos > 0)

    return {
        "pendientes": [_resumen_carga(f) for f in pagina.items if _pendiente(f)],
    }


async def obtener_preferencias(
    session: AsyncSession,
    permisos: PermisosEfectivos,
    actor_user_id: UUID,
    company_id: UUID | None,
    argumentos: dict[str, Any],
) -> dict[str, Any]:
    columnas = await columnas_usuario.obtener(session, user_id=actor_user_id, vista="shipments")
    return {"columnas_visibles": columnas}


def _resumen_despacho(fila: Any) -> dict[str, Any]:
    return {
        "dispatch_number": fila.dispatch_number,
        "estado": fila.status,
        "metodo": fila.method,
        "cargas": fila.cargas,
        "solicitado_el": fila.requested_at,
    }


async def consultar_despacho(
    session: AsyncSession,
    permisos: PermisosEfectivos,
    actor_user_id: UUID,
    company_id: UUID | None,
    argumentos: dict[str, Any],
) -> dict[str, Any]:
    alcance = alcance_de_lectura(permisos)
    empresas = None if alcance.global_ else alcance.company_ids
    try:
        fila = await dispatches_queries.detalle_por_numero(
            session, dispatch_number=argumentos["dispatch_number"], empresas=empresas
        )
    except RecursoNoEncontrado:
        return {"encontrado": False}

    return {
        "encontrado": True,
        "dispatch_number": fila.dispatch_number,
        "estado": fila.status,
        "metodo": fila.method,
        "cargas": fila.shipments,
        "solicitado_el": fila.requested_at,
        "aprobado_el": fila.approved_at,
        "despachado_el": fila.dispatched_at,
        "completado_el": fila.completed_at,
        "motivo_rechazo": fila.rejected_reason,
    }


async def listar_despachos(
    session: AsyncSession,
    permisos: PermisosEfectivos,
    actor_user_id: UUID,
    company_id: UUID | None,
    argumentos: dict[str, Any],
) -> dict[str, Any]:
    alcance = alcance_de_lectura(permisos)
    empresas = None if alcance.global_ else alcance.company_ids
    estado = argumentos.get("estado")

    pagina = await dispatches_queries.listar(
        session,
        empresas=empresas,
        limit=_LIMITE_LISTAR_DESPACHOS,
        cursor=None,
        status_filtro=[estado] if estado else None,
    )
    return {
        "despachos": [_resumen_despacho(f) for f in pagina.items],
        "hay_mas": pagina.has_more,
    }


async def como_hago(
    session: AsyncSession,
    permisos: PermisosEfectivos,
    actor_user_id: UUID,
    company_id: UUID | None,
    argumentos: dict[str, Any],
) -> dict[str, Any]:
    """`copilot/conocimiento/*.md` (ADR-0012, Fase 3): un tema sin entrada NO
    se contesta con una adivinanza — el system prompt ya instruye no inventar
    datos, y esto aplica la misma regla a la guía de producto."""
    entrada = base_de_conocimiento.buscar(argumentos["tema"])
    if entrada is None:
        return {
            "tiene_respuesta": False,
            "mensaje": "Todavía no tengo una guía escrita para eso. Consultá con Operaciones.",
        }
    return {"tiene_respuesta": True, "titulo": entrada.titulo, "respuesta": entrada.cuerpo}


REGISTRO_EJECUTORES: dict[str, EjecutorHerramienta] = {
    "consultar_estado_carga": consultar_estado_carga,
    "buscar_cargas": buscar_cargas,
    "explicar_que_falta": explicar_que_falta,
    "mis_pendientes": mis_pendientes,
    "obtener_preferencias": obtener_preferencias,
    "consultar_despacho": consultar_despacho,
    "listar_despachos": listar_despachos,
    "como_hago": como_hago,
}

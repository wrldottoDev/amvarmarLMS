"""Consultas de lectura de solicitudes de despacho.

Extraídas de `router.py` (Fase 3, ADR-0012): antes vivían inline dentro del
endpoint `listar`. Separarlas permite que el router y las herramientas de
lectura del asistente (`copilot/executors.py`) usen la MISMA consulta — dos
copias de este SQL solo pueden divergir con el tiempo.

No hay `dispatch_requests.read`: el alcance de qué solicitudes ve un actor
sale de `shipments.read` (`alcance_de_lectura`), igual que el resto de lectura
de cargas. Por eso estas funciones reciben `empresas: list[UUID] | None`
(`None` = alcance global) en vez de un permiso propio que verificar.
"""

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import RecursoNoEncontrado
from app.core.pagination import Cursor, Pagina, armar_pagina, normalizar_limite


async def listar(
    session: AsyncSession,
    *,
    empresas: list[UUID] | None,
    limit: int | None,
    cursor: str | None,
    status_filtro: list[str] | None,
) -> Pagina[Any]:
    if empresas is not None and not empresas:
        return Pagina(items=[], next_cursor=None, has_more=False)

    limite = normalizar_limite(limit)
    condiciones: list[str] = []
    parametros: dict[str, Any] = {"limite": limite + 1}

    if empresas is not None:
        condiciones.append("d.company_id = ANY(:empresas)")
        parametros["empresas"] = empresas

    if status_filtro:
        condiciones.append("d.status = ANY(:estados)")
        parametros["estados"] = status_filtro

    if cursor:
        posicion = Cursor.decodificar(cursor)
        condiciones.append("(d.requested_at, d.id) < (:cursor_fecha, :cursor_id)")
        parametros["cursor_fecha"] = posicion.created_at
        parametros["cursor_id"] = posicion.id

    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    consulta = f"""
        SELECT d.id, d.dispatch_number, d.status, d.method, d.company_id,
               d.requested_at, d.row_version,
               count(s.shipment_id) AS cargas
        FROM dispatch_requests d
        LEFT JOIN dispatch_request_shipments s ON s.dispatch_request_id = d.id
        {where}
        GROUP BY d.id
        ORDER BY d.requested_at DESC, d.id DESC
        LIMIT :limite
    """  # noqa: S608

    filas = list((await session.execute(text(consulta), parametros)).all())
    return armar_pagina(
        filas, limite=limite, cursor_de=lambda f: Cursor(created_at=f.requested_at, id=f.id)
    )


async def detalle(session: AsyncSession, *, dispatch_id: UUID, empresas: list[UUID] | None) -> Any:
    condiciones = ["d.id = :id"]
    parametros: dict[str, Any] = {"id": dispatch_id}
    if empresas is not None:
        condiciones.append("d.company_id = ANY(:empresas)")
        parametros["empresas"] = empresas

    fila = await _detalle_por_condicion(session, condiciones, parametros)
    if fila is None:
        raise RecursoNoEncontrado("Solicitud de despacho no encontrada.")
    return fila


async def detalle_por_numero(
    session: AsyncSession, *, dispatch_number: str, empresas: list[UUID] | None
) -> Any:
    """Para la herramienta `consultar_despacho`: el modelo conoce el número
    legible (DSP-2026-000123), no el UUID interno."""
    condiciones = ["d.dispatch_number = :numero"]
    parametros: dict[str, Any] = {"numero": dispatch_number}
    if empresas is not None:
        condiciones.append("d.company_id = ANY(:empresas)")
        parametros["empresas"] = empresas

    fila = await _detalle_por_condicion(session, condiciones, parametros)
    if fila is None:
        raise RecursoNoEncontrado("Solicitud de despacho no encontrada.")
    return fila


async def _detalle_por_condicion(
    session: AsyncSession, condiciones: list[str], parametros: dict[str, Any]
) -> Any:
    consulta = f"""
        SELECT d.id, d.dispatch_number, d.status, d.method, d.company_id,
               d.delivery_address, d.instructions, d.requested_pickup_date,
               d.rejected_reason, d.requested_at, d.approved_at, d.dispatched_at,
               d.completed_at,
               d.row_version,
               COALESCE(
                   array_agg(s.shipment_id ORDER BY s.shipment_id)
                   FILTER (WHERE s.shipment_id IS NOT NULL),
                   '{{}}'
               ) AS shipment_ids,
               COALESCE(
                   jsonb_agg(
                       jsonb_build_object(
                           'id', sh.id,
                           'shipment_number', sh.shipment_number,
                           'wr', (
                               SELECT r.value
                               FROM shipment_references r
                               WHERE r.shipment_id = sh.id AND r.reference_type = 'WR'
                               ORDER BY r.is_primary DESC, r.created_at
                               LIMIT 1
                           ),
                           'invoice', (
                               SELECT r.value
                               FROM shipment_references r
                               WHERE r.shipment_id = sh.id AND r.reference_type = 'INVOICE'
                               ORDER BY r.is_primary DESC, r.created_at
                               LIMIT 1
                           ),
                           'status', sh.current_status_code,
                           'package_count', sh.package_count,
                           'weight_kg', sh.weight_kg
                       ) ORDER BY sh.shipment_number
                   ) FILTER (WHERE sh.id IS NOT NULL),
                   '[]'::jsonb
               ) AS shipments
        FROM dispatch_requests d
        LEFT JOIN dispatch_request_shipments s ON s.dispatch_request_id = d.id
        LEFT JOIN shipments sh ON sh.id = s.shipment_id
        WHERE {" AND ".join(condiciones)}
        GROUP BY d.id
    """  # noqa: S608

    return (await session.execute(text(consulta), parametros)).one_or_none()

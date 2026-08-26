"""Consultas de lectura: listados, detalle, timeline y dashboard.

El aislamiento por empresa se resuelve aquí, en el WHERE, no filtrando después
en Python: una consulta que trae filas ajenas y las descarta al final ya las
expuso al proceso, y un `LIMIT` mal puesto las devolvería.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import RecursoNoEncontrado
from app.core.pagination import Cursor, Pagina, armar_pagina
from app.modules.rbac.catalog import Perm
from app.modules.rbac.models import ScopeType
from app.modules.rbac.service import PermisosEfectivos


@dataclass(frozen=True)
class AlcanceDeLectura:
    """Qué cargas puede ver el actor.

    `company_ids is None` significa alcance global. Una lista vacía significa
    que no puede ver nada — distinto de global, y por eso no se usa `None` para
    ambos casos.
    """

    global_: bool
    company_ids: list[UUID]

    @property
    def no_ve_nada(self) -> bool:
        return not self.global_ and not self.company_ids


def alcance_de_lectura(permisos: PermisosEfectivos) -> AlcanceDeLectura:
    empresas: list[UUID] = []
    for permiso in permisos.permisos:
        if permiso.code != Perm.SHIPMENTS_READ:
            continue
        if permiso.scope_type == ScopeType.GLOBAL:
            return AlcanceDeLectura(global_=True, company_ids=[])
        if permiso.scope_type == ScopeType.ORGANIZATION and permiso.company_id:
            empresas.append(permiso.company_id)

    return AlcanceDeLectura(global_=False, company_ids=empresas)


@dataclass(frozen=True)
class FiltrosListado:
    estados: list[str] | None = None
    company_id: UUID | None = None
    eta_desde: datetime | None = None
    eta_hasta: datetime | None = None
    # Busca en shipment_number y en el valor de cualquier referencia.
    texto: str | None = None
    incluir_archivadas: bool = False
    # El "eliminar" del sistema viejo. Por defecto no se listan; Operaciones
    # puede pedirlas para revisarlas o recuperarlas.
    incluir_ocultas: bool = False


# Columnas del listado. Explícitas y no `SELECT *`: agregar una columna a
# `shipments` no debe filtrarla sin querer a la respuesta.
_COLUMNAS_LISTADO = """
    s.id,
    s.shipment_number,
    s.company_id,
    s.current_status_code,
    s.transport_mode,
    s.estimated_arrival_at,
    s.current_location,
    s.package_count,
    s.weight_kg,
    s.weight_lb,
    s.foots_cft,
    s.shipper,
    s.carrier,
    s.hidden_at,
    s.permit_review_required,
    s.legacy_review_required,
    s.created_at,
    s.updated_at,
    s.origin_location_id AS origen_id,
    s.destination_location_id AS destino_id,
    origen.location_code AS origen_codigo,
    origen.name AS origen_nombre,
    origen.country_code AS origen_pais,
    destino.location_code AS destino_codigo,
    destino.name AS destino_nombre,
    destino.country_code AS destino_pais,
    (
        SELECT r.value FROM shipment_references r
        WHERE r.shipment_id = s.id AND r.reference_type = 'INVOICE'
        ORDER BY r.is_primary DESC, r.created_at
        LIMIT 1
    ) AS factura,
    -- Tracking, PO y contenedor salen de las referencias, igual que la factura:
    -- una carga puede tener varias, y el sistema viejo las metía separadas por
    -- comas en un solo campo de texto.
    (
        SELECT r.value FROM shipment_references r
        WHERE r.shipment_id = s.id AND r.reference_type = 'TRACKING'
        ORDER BY r.is_primary DESC, r.created_at LIMIT 1
    ) AS tracking,
    (
        SELECT r.value FROM shipment_references r
        WHERE r.shipment_id = s.id AND r.reference_type = 'PO'
        ORDER BY r.is_primary DESC, r.created_at LIMIT 1
    ) AS po,
    (
        SELECT r.value FROM shipment_references r
        WHERE r.shipment_id = s.id AND r.reference_type = 'CONTAINER'
        ORDER BY r.is_primary DESC, r.created_at LIMIT 1
    ) AS contenedor,
    (
        SELECT r.value FROM shipment_references r
        WHERE r.shipment_id = s.id AND r.reference_type = 'WR'
        ORDER BY r.is_primary DESC, r.created_at LIMIT 1
    ) AS wr,
    (
        SELECT count(*) FROM shipment_requirements q
        WHERE q.shipment_id = s.id
          AND q.status IN ('OPEN', 'PENDING', 'UPLOADED', 'REJECTED')
    ) AS requisitos_abiertos,
    (
        SELECT count(*) FROM shipment_requirements q
        WHERE q.shipment_id = s.id
          AND q.required_from = 'CLIENT'
          AND q.status IN ('OPEN', 'PENDING', 'UPLOADED', 'REJECTED')
    ) AS requisitos_del_cliente
"""


async def listar_shipments(
    session: AsyncSession,
    *,
    permisos: PermisosEfectivos,
    filtros: FiltrosListado,
    limite: int,
    cursor: Cursor | None = None,
) -> Pagina[Any]:
    alcance = alcance_de_lectura(permisos)
    if alcance.no_ve_nada:
        return Pagina(items=[], next_cursor=None, has_more=False)

    condiciones = ["s.deleted_at IS NULL"]
    if not filtros.incluir_ocultas:
        condiciones.append("s.hidden_at IS NULL")
    parametros: dict[str, Any] = {"limite": limite + 1}

    if not filtros.incluir_archivadas:
        # ADR-0007: lo archivado sale del flujo operativo y solo aparece en
        # `Historial de despachos`.
        condiciones.append("s.archived_at IS NULL")

    if not alcance.global_:
        condiciones.append("s.company_id = ANY(:empresas)")
        parametros["empresas"] = alcance.company_ids

    if filtros.company_id is not None:
        # Filtro adicional del usuario. NO reemplaza al de alcance: se suman,
        # así un cliente que pida otra empresa no obtiene nada en vez de todo.
        condiciones.append("s.company_id = :company_id")
        parametros["company_id"] = filtros.company_id

    if filtros.estados:
        condiciones.append("s.current_status_code = ANY(:estados)")
        parametros["estados"] = filtros.estados

    if filtros.eta_desde is not None:
        condiciones.append("s.estimated_arrival_at >= :eta_desde")
        parametros["eta_desde"] = filtros.eta_desde

    if filtros.eta_hasta is not None:
        condiciones.append("s.estimated_arrival_at <= :eta_hasta")
        parametros["eta_hasta"] = filtros.eta_hasta

    if filtros.texto:
        condiciones.append("""(
            s.shipment_number ILIKE :texto
            OR EXISTS (
                SELECT 1 FROM shipment_references r
                WHERE r.shipment_id = s.id AND r.value ILIKE :texto
            )
        )""")
        parametros["texto"] = f"%{filtros.texto}%"

    if cursor is not None:
        # Keyset: la comparación de tuplas es lo que hace la paginación estable
        # aunque se inserten filas mientras se recorre.
        condiciones.append("(s.created_at, s.id) < (:cursor_fecha, :cursor_id)")
        parametros["cursor_fecha"] = cursor.created_at
        parametros["cursor_id"] = cursor.id

    consulta = f"""
        SELECT {_COLUMNAS_LISTADO}
        FROM shipments s
        JOIN locations origen ON origen.id = s.origin_location_id
        JOIN locations destino ON destino.id = s.destination_location_id
        WHERE {" AND ".join(condiciones)}
        ORDER BY s.created_at DESC, s.id DESC
        LIMIT :limite
    """  # noqa: S608

    filas = list((await session.execute(text(consulta), parametros)).all())

    return armar_pagina(
        filas,
        limite=limite,
        cursor_de=lambda fila: Cursor(created_at=fila.created_at, id=fila.id),
    )


async def obtener_shipment(
    session: AsyncSession, *, shipment_id: UUID, permisos: PermisosEfectivos
) -> Any:
    """Detalle de una carga. `404` si no existe o si es de otra empresa."""
    alcance = alcance_de_lectura(permisos)
    if alcance.no_ve_nada:
        raise RecursoNoEncontrado("Carga no encontrada.")

    condiciones = ["s.id = :shipment_id", "s.deleted_at IS NULL"]
    parametros: dict[str, Any] = {"shipment_id": shipment_id}

    if not alcance.global_:
        condiciones.append("s.company_id = ANY(:empresas)")
        parametros["empresas"] = alcance.company_ids

    # Las condiciones salen de literales de este módulo, nunca de entrada
    # externa; los valores sí van parametrizados.
    consulta = f"""
        SELECT {_COLUMNAS_LISTADO},
               s.row_version,
               s.description,
               s.received_at, s.stored_at, s.dispatched_at, s.delivered_at,
               s.destination_address
        FROM shipments s
        JOIN locations origen ON origen.id = s.origin_location_id
        JOIN locations destino ON destino.id = s.destination_location_id
        WHERE {" AND ".join(condiciones)}
    """  # noqa: S608

    fila = (await session.execute(text(consulta), parametros)).one_or_none()

    if fila is None:
        # Ajeno e inexistente son indistinguibles desde afuera.
        raise RecursoNoEncontrado("Carga no encontrada.")

    return fila


async def timeline(
    session: AsyncSession,
    *,
    shipment_id: UUID,
    permisos: PermisosEfectivos,
    limite: int,
    cursor: Cursor | None = None,
) -> Pagina[Any]:
    # Verifica acceso antes de leer la historia: sin esto, el timeline sería
    # una vía lateral para ver cargas ajenas.
    await obtener_shipment(session, shipment_id=shipment_id, permisos=permisos)

    condiciones = ["e.shipment_id = :shipment_id"]
    parametros: dict[str, Any] = {"shipment_id": shipment_id, "limite": limite + 1}

    if cursor is not None:
        condiciones.append("(e.occurred_at, e.id) < (:cursor_fecha, :cursor_id)")
        parametros["cursor_fecha"] = cursor.created_at
        parametros["cursor_id"] = cursor.id

    consulta = f"""
        SELECT e.id, e.event_type, e.from_status_code, e.to_status_code,
               e.title, e.description, e.location, e.occurred_at,
               e.recorded_at, e.actor_user_id,
               u.first_name, u.last_name
        FROM shipment_events e
        LEFT JOIN users u ON u.id = e.actor_user_id
        WHERE {" AND ".join(condiciones)}
        ORDER BY e.occurred_at DESC, e.id DESC
        LIMIT :limite
    """  # noqa: S608

    filas = list((await session.execute(text(consulta), parametros)).all())

    return armar_pagina(
        filas,
        limite=limite,
        cursor_de=lambda fila: Cursor(created_at=fila.occurred_at, id=fila.id),
    )


@dataclass(frozen=True)
class TarjetasDashboard:
    en_bodega: int
    en_transito: int
    proximos_a_llegar: int
    requieren_accion: int
    entregados_este_mes: int


# Ventana de "próximos a llegar". Configurable en el futuro vía system_settings.
DIAS_PROXIMA_LLEGADA = 7

# Zona horaria de la operación: "este mes" se cuenta en Costa Rica, no en UTC.
# Una entrega del 31 a las 20:00 hora local es del mes que cierra, no del
# siguiente (sección 6 del documento de arquitectura).
ZONA_OPERACION = "America/Costa_Rica"


async def tarjetas_dashboard(
    session: AsyncSession, *, permisos: PermisosEfectivos, solo_del_cliente: bool
) -> TarjetasDashboard:
    """Las 5 tarjetas de la sección 6.

    `solo_del_cliente` cambia qué cuenta "requieren acción": el cliente ve lo
    que le toca a él; Operaciones, todo lo pendiente.
    """
    alcance = alcance_de_lectura(permisos)
    if alcance.no_ve_nada:
        return TarjetasDashboard(0, 0, 0, 0, 0)

    filtro_empresa = "" if alcance.global_ else "AND s.company_id = ANY(:empresas)"
    parametros: dict[str, Any] = {
        "dias": DIAS_PROXIMA_LLEGADA,
        "zona": ZONA_OPERACION,
    }
    if not alcance.global_:
        parametros["empresas"] = alcance.company_ids

    filtro_requisito = "AND q.required_from = 'CLIENT'" if solo_del_cliente else ""

    fila = (
        await session.execute(
            text(f"""
                SELECT
                    count(*) FILTER (
                        WHERE s.current_status_code IN ('RECEIVED', 'STORED')
                    ) AS en_bodega,
                    count(*) FILTER (
                        WHERE s.current_status_code = 'IN_TRANSIT'
                    ) AS en_transito,
                    count(*) FILTER (
                        WHERE s.estimated_arrival_at IS NOT NULL
                          AND s.estimated_arrival_at
                              BETWEEN now() AND now() + make_interval(days => :dias)
                          AND st.is_terminal = false
                    ) AS proximos_a_llegar,
                    count(*) FILTER (
                        WHERE EXISTS (
                            SELECT 1 FROM shipment_requirements q
                            WHERE q.shipment_id = s.id
                              AND q.status IN ('OPEN', 'PENDING', 'UPLOADED', 'REJECTED')
                              {filtro_requisito}
                        )
                    ) AS requieren_accion,
                    count(*) FILTER (
                        WHERE s.current_status_code = 'DELIVERED'
                          AND date_trunc('month', s.delivered_at AT TIME ZONE :zona)
                              = date_trunc('month', now() AT TIME ZONE :zona)
                    ) AS entregados_este_mes
                FROM shipments s
                JOIN shipment_statuses st ON st.code = s.current_status_code
                WHERE s.deleted_at IS NULL
                  AND s.archived_at IS NULL
                  {filtro_empresa}
            """),  # noqa: S608
            parametros,
        )
    ).one()

    return TarjetasDashboard(
        en_bodega=fila.en_bodega,
        en_transito=fila.en_transito,
        proximos_a_llegar=fila.proximos_a_llegar,
        requieren_accion=fila.requieren_accion,
        entregados_este_mes=fila.entregados_este_mes,
    )


async def proximos_movimientos(
    session: AsyncSession, *, permisos: PermisosEfectivos, limite: int = 10
) -> list[Any]:
    """Cargas con ETA cercana, ordenadas por llegada.

    Devuelve `status` y el conteo de pendientes como campos SEPARADOS: "faltan
    documentos" nunca reemplaza al estado logístico en la interfaz.
    """
    alcance = alcance_de_lectura(permisos)
    if alcance.no_ve_nada:
        return []

    condiciones = [
        "s.deleted_at IS NULL",
        "s.archived_at IS NULL",
        "st.is_terminal = false",
        "s.estimated_arrival_at IS NOT NULL",
    ]
    parametros: dict[str, Any] = {"limite": limite}

    if not alcance.global_:
        condiciones.append("s.company_id = ANY(:empresas)")
        parametros["empresas"] = alcance.company_ids

    return list(
        (
            await session.execute(
                text(f"""
                    SELECT {_COLUMNAS_LISTADO}
                    FROM shipments s
                    JOIN shipment_statuses st ON st.code = s.current_status_code
                    JOIN locations origen ON origen.id = s.origin_location_id
                    JOIN locations destino ON destino.id = s.destination_location_id
                    WHERE {" AND ".join(condiciones)}
                    ORDER BY s.estimated_arrival_at, s.id
                    LIMIT :limite
                """),  # noqa: S608
                parametros,
            )
        ).all()
    )


async def bultos(session: AsyncSession, shipment_id: UUID) -> list[Any]:
    """Las piezas declaradas de una carga.

    Consulta aparte y no un JOIN en el detalle: son varias filas por carga y
    traerlas mezcladas obligaría a desduplicar el resto de las columnas.
    El permiso ya se comprobó al leer la carga.
    """
    return list(
        (
            await session.execute(
                text("""
                    SELECT id, package_type, quantity, description, weight_kg
                    FROM shipment_packages
                    WHERE shipment_id = :s
                    ORDER BY created_at, id
                """),
                {"s": shipment_id},
            )
        ).all()
    )

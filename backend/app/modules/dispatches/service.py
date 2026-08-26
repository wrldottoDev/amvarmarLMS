"""Solicitudes de despacho (Paso 3.3).

Cada acción mueve la solicitud Y las cargas que reclama, en una sola
transacción. Que el despacho quede aprobado con las cargas en el estado viejo
—o al revés— sería un expediente que se contradice a sí mismo.

Los cambios de estado de las cargas pasan por el motor de transiciones
(`shipments.service.transicionar`), no por un UPDATE directo: así el catálogo,
los permisos y la línea de tiempo valen igual venga el cambio de donde venga.
"""

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflicto, RecursoNoEncontrado, ReglaDeNegocioViolada
from app.modules.audit.outbox import publicar
from app.modules.dispatches.models import (
    ESTADOS_FINALES,
    DispatchEventType,
    DispatchStatus,
)
from app.modules.rbac.catalog import Perm
from app.modules.rbac.models import ScopeType
from app.modules.rbac.service import PermisoEfectivo, PermisosEfectivos
from app.modules.shipments import service as shipments
from app.modules.shipments.models import ShipmentStatus


class CargaNoDisponible(Conflicto):
    """La carga ya está reclamada por otra solicitud activa."""

    code = "SHIPMENT_YA_EN_DESPACHO"


class TransicionDeDespachoInvalida(Conflicto):
    code = "DISPATCH_TRANSITION_INVALID"


class MotivoRequerido(ReglaDeNegocioViolada):
    code = "DISPATCH_EXIGE_MOTIVO"


class DespachoSinCargas(ReglaDeNegocioViolada):
    code = "DISPATCH_SIN_CARGAS"


class SinPermisoParaDespacho(Exception):
    """El router decide si responder 403 o 404 y registra el intento."""


class CancelacionNoPermitida(Conflicto):
    """El cliente intenta cancelar una solicitud ya aprobada (ADR-0013)."""

    code = "DISPATCH_YA_APROBADO"


# ADR-0013: desde qué estados puede cancelar cada tipo de actor.
#
# Una vez que Operaciones aprobó, el cliente ya no cancela: puede haber
# contenedor reservado o transporte contratado. Si necesita detenerlo, lo pide
# a Operaciones, que decide.
_CANCELABLE_POR_CLIENTE: frozenset[str] = frozenset({DispatchStatus.PENDING})
_CANCELABLE_POR_OPERACIONES: frozenset[str] = frozenset(
    {DispatchStatus.PENDING, DispatchStatus.APPROVED, DispatchStatus.PREPARING}
)


# Transiciones válidas de la solicitud. Declaradas en código y no en tabla
# porque, a diferencia de los estados de carga, son pocas y no las configura
# nadie desde la interfaz.
_TRANSICIONES: dict[str, frozenset[str]] = {
    DispatchStatus.PENDING: frozenset(
        {DispatchStatus.APPROVED, DispatchStatus.REJECTED, DispatchStatus.CANCELLED}
    ),
    DispatchStatus.APPROVED: frozenset({DispatchStatus.PREPARING, DispatchStatus.CANCELLED}),
    DispatchStatus.PREPARING: frozenset({DispatchStatus.COMPLETED, DispatchStatus.CANCELLED}),
    DispatchStatus.DISPATCHED: frozenset({DispatchStatus.COMPLETED}),
    DispatchStatus.COMPLETED: frozenset(),
    DispatchStatus.REJECTED: frozenset(),
    DispatchStatus.CANCELLED: frozenset(),
}

# Estado al que se mueven las cargas con cada acción del despacho.
# Estados de la solicitud que exigen tener los documentos obligatorios listos.
_EXIGEN_REQUISITOS: frozenset[str] = frozenset({DispatchStatus.APPROVED, DispatchStatus.COMPLETED})

_ESTADO_DE_CARGA: dict[str, str] = {
    DispatchStatus.PREPARING: ShipmentStatus.PREPARING,
    DispatchStatus.COMPLETED: ShipmentStatus.DISPATCHED,
}


def _permisos_de_la_accion(
    permisos: PermisosEfectivos, company_id: UUID, *codigos: str
) -> PermisosEfectivos:
    """Permisos del paso interno que mueve las cargas de un despacho (ADR-0013).

    Una acción de despacho autorizada arrastra el movimiento de sus cargas: es
    la consecuencia de una sola decisión, no una segunda operación que deba
    pedir su propio permiso.

    Sin esto, un cliente no podría ni crear ni cancelar una solicitud: tiene
    `dispatch_requests.create` y `.cancel` pero ningún permiso de transición de
    carga, y ambas acciones mueven estados.

    Se construye explícitamente, con los códigos que la acción necesita y
    acotado a esta empresa. NO existe una bandera global de "saltear permisos",
    y mover una carga a mano fuera de un despacho sigue exigiendo su permiso
    propio.
    """
    return PermisosEfectivos(
        user_id=permisos.user_id,
        authz_version=permisos.authz_version,
        permisos=(
            *permisos.permisos,
            *(
                PermisoEfectivo(
                    code=codigo,
                    scope_type=ScopeType.ORGANIZATION,
                    company_id=company_id,
                )
                for codigo in codigos
            ),
        ),
    )


@dataclass(frozen=True)
class SolicitudCreada:
    id: UUID
    dispatch_number: str
    status: str
    shipment_ids: list[UUID]


@dataclass(frozen=True)
class ResultadoAccion:
    id: UUID
    desde: str
    hacia: str
    row_version: int


async def crear(
    session: AsyncSession,
    *,
    company_id: UUID,
    actor_user_id: UUID,
    method: str,
    shipment_ids: list[UUID],
    permisos: PermisosEfectivos,
    delivery_address: str | None = None,
    instructions: str | None = None,
    requested_pickup_date: date | None = None,
) -> SolicitudCreada:
    """Crea la solicitud y reclama las cargas.

    Todo el reclamo ocurre en una transacción: o se toman todas las cargas o
    ninguna. Una solicitud a medias dejaría cargas bloqueadas sin despacho que
    las use.
    """
    if not shipment_ids:
        raise DespachoSinCargas("Agregá al menos una carga a la solicitud.")

    # Orden estable por id: dos solicitudes concurrentes que compartan cargas
    # las bloquean en el mismo orden y no se traban entre sí (deadlock).
    ordenadas = sorted(set(shipment_ids))

    cargas = list(
        (
            await session.execute(
                text("""
                    SELECT id, company_id, current_status_code, shipment_number
                    FROM shipments
                    WHERE id = ANY(:ids) AND deleted_at IS NULL
                    ORDER BY id
                    FOR UPDATE
                """),
                {"ids": ordenadas},
            )
        ).all()
    )

    if len(cargas) != len(ordenadas):
        raise RecursoNoEncontrado("Alguna de las cargas no existe.")

    for carga in cargas:
        if carga.company_id != company_id:
            # Mezclar empresas en un despacho expondría datos de una a la otra.
            raise RecursoNoEncontrado("Alguna de las cargas no existe.")
        if carga.current_status_code != ShipmentStatus.STORED:
            raise TransicionDeDespachoInvalida(
                f"La carga {carga.shipment_number} está en "
                f"{carga.current_status_code} y solo se despachan las almacenadas.",
                details=[
                    {
                        "shipment_number": carga.shipment_number,
                        "status": carga.current_status_code,
                    }
                ],
            )

    dispatch = (
        await session.execute(
            text("""
                INSERT INTO dispatch_requests (
                    company_id, requested_by, method, status,
                    delivery_address, instructions, requested_pickup_date
                )
                VALUES (:c, :u, :method, :estado, :direccion, :instrucciones, :fecha)
                RETURNING id, dispatch_number
            """),
            {
                "c": company_id,
                "u": actor_user_id,
                "method": method,
                "estado": DispatchStatus.PENDING.value,
                "direccion": delivery_address,
                "instrucciones": instructions,
                "fecha": requested_pickup_date,
            },
        )
    ).one()

    try:
        for shipment_id in ordenadas:
            await session.execute(
                text("""
                    INSERT INTO dispatch_request_shipments (dispatch_request_id, shipment_id)
                    VALUES (:d, :s)
                """),
                {"d": dispatch.id, "s": shipment_id},
            )
    except IntegrityError as error:
        # El índice único parcial rechazó una carga ya reclamada. Es la garantía
        # real: vale aunque el FOR UPDATE de arriba no hubiera alcanzado.
        raise CargaNoDisponible(
            "Alguna de las cargas ya está en otra solicitud de despacho activa."
        ) from error

    # Las cargas pasan a DISPATCH_REQUESTED por el motor de transiciones, para
    # que quede su evento en la línea de tiempo como cualquier otro cambio.
    permisos_creacion = _permisos_de_la_accion(
        permisos, company_id, Perm.SHIPMENTS_TRANSITION_FORWARD
    )
    for carga in cargas:
        await _mover_carga(
            session,
            shipment_id=carga.id,
            hacia=ShipmentStatus.DISPATCH_REQUESTED,
            actor_user_id=actor_user_id,
            permisos=permisos_creacion,
            nota=f"Incluida en la solicitud {dispatch.dispatch_number}",
        )

    await _registrar_evento(
        session,
        dispatch_id=dispatch.id,
        tipo=DispatchEventType.CREATED,
        desde=None,
        hacia=DispatchStatus.PENDING.value,
        actor_user_id=actor_user_id,
        notas=instructions,
        datos={"cargas": len(ordenadas)},
    )

    # Dispara los dos avisos de la creación: el acuse al cliente y el pedido de
    # aprobación a Operaciones. Va por el outbox y no por un envío directo acá
    # para que un relay caído no haga fallar la creación de la solicitud.
    #
    # La `dedup_key` usa la versión inicial de la fila, igual que las
    # transiciones (`dispatch:{id}:v{n}`), así que una solicitud tiene una sola
    # cadena de claves sin huecos ni choques.
    await publicar(
        session,
        aggregate_type="dispatch_request",
        aggregate_id=dispatch.id,
        event_type="dispatch.created",
        payload={
            "hacia": DispatchStatus.PENDING.value,
            "company_id": str(company_id),
            "actor_user_id": str(actor_user_id),
            "cargas": len(ordenadas),
        },
        dedup_key=f"dispatch:{dispatch.id}:v1",
    )

    return SolicitudCreada(
        id=dispatch.id,
        dispatch_number=dispatch.dispatch_number,
        status=DispatchStatus.PENDING.value,
        shipment_ids=ordenadas,
    )


async def _mover_carga(
    session: AsyncSession,
    *,
    shipment_id: UUID,
    hacia: str,
    actor_user_id: UUID,
    permisos: PermisosEfectivos,
    nota: str | None = None,
) -> None:
    """Cambia el estado de una carga a través del motor de transiciones.

    Se lee `row_version` acá y no se pide al llamador: el bloqueo optimista
    protege contra ediciones concurrentes de un usuario, y esta es una
    orquestación interna que ya tiene la fila bloqueada con FOR UPDATE.
    """
    version = (
        await session.execute(
            text("SELECT row_version FROM shipments WHERE id = :id"), {"id": shipment_id}
        )
    ).scalar_one()

    await shipments.transicionar(
        session,
        shipment_id=shipment_id,
        datos=shipments.DatosTransicion(to_status=hacia, row_version=version, note=nota),
        actor_user_id=actor_user_id,
        permisos=permisos,
    )


async def _cargar_solicitud(
    session: AsyncSession, dispatch_id: UUID, company_ids: list[UUID] | None
) -> Any:
    condiciones = ["id = :id"]
    parametros: dict[str, object] = {"id": dispatch_id}
    if company_ids is not None:
        condiciones.append("company_id = ANY(:empresas)")
        parametros["empresas"] = company_ids

    consulta = f"""
        SELECT id, company_id, status, dispatch_number, row_version
        FROM dispatch_requests
        WHERE {" AND ".join(condiciones)}
        FOR UPDATE
    """  # noqa: S608

    fila = (await session.execute(text(consulta), parametros)).one_or_none()
    if fila is None:
        raise RecursoNoEncontrado("Solicitud de despacho no encontrada.")
    return fila


async def _cambiar_estado(
    session: AsyncSession,
    *,
    dispatch_id: UUID,
    hacia: str,
    actor_user_id: UUID,
    permisos: PermisosEfectivos,
    company_ids: list[UUID] | None,
    tipo_evento: DispatchEventType,
    motivo: str | None = None,
    row_version: int | None = None,
) -> ResultadoAccion:
    solicitud = await _cargar_solicitud(session, dispatch_id, company_ids)
    desde = solicitud.status

    if hacia not in _TRANSICIONES[desde]:
        raise TransicionDeDespachoInvalida(
            f"No se puede pasar de {desde} a {hacia}.",
            details=[{"from": desde, "allowed": sorted(_TRANSICIONES[desde])}],
        )

    if row_version is not None and solicitud.row_version != row_version:
        raise Conflicto(
            "La solicitud fue modificada por otra persona. Recargá y reintentá.",
            code="DISPATCH_VERSION_CONFLICT",
            details=[{"row_version_actual": solicitud.row_version}],
        )

    if hacia == DispatchStatus.REJECTED and not (motivo or "").strip():
        raise MotivoRequerido("Rechazar una solicitud exige indicar el motivo.")

    cargas = await _cargas_activas(session, dispatch_id)

    if hacia == DispatchStatus.COMPLETED and not cargas:
        # Cerrar una solicitud vacía dejaría un despacho que no despachó nada.
        raise DespachoSinCargas("La solicitud no tiene cargas asociadas.")

    # Los documentos obligatorios se exigen al APROBAR, no solo al completar.
    # Aprobar es lo que dispara la coordinación real (contenedor, transporte),
    # así que descubrir ahí que falta la factura es mucho más barato que
    # descubrirlo con la carga ya en preparación. Se vuelve a verificar al
    # completar porque entre una cosa y otra alguien pudo rechazar un documento.
    if hacia in _EXIGEN_REQUISITOS and cargas:
        await shipments.validar_requisitos_de_cargas(session, cargas, ShipmentStatus.DISPATCHED)

    nueva_version = (
        await session.execute(
            # `:hacia` se castea explícitamente: sin el CAST, asyncpg lo deduce
            # como varchar en el SET y como text en los CASE, y falla por tipos
            # inconsistentes para el mismo parámetro.
            text("""
                UPDATE dispatch_requests
                SET status = CAST(:hacia AS VARCHAR),
                    row_version = row_version + 1,
                    updated_at = now(),
                    rejected_reason = CASE WHEN CAST(:hacia AS VARCHAR) = 'REJECTED'
                                           THEN :motivo ELSE rejected_reason END,
                    approved_by = CASE WHEN CAST(:hacia AS VARCHAR) = 'APPROVED'
                                       THEN :actor ELSE approved_by END,
                    approved_at = CASE WHEN CAST(:hacia AS VARCHAR) = 'APPROVED'
                                       THEN now() ELSE approved_at END,
                    completed_at = CASE WHEN CAST(:hacia AS VARCHAR) = 'COMPLETED'
                                        THEN now() ELSE completed_at END
                WHERE id = :id
                RETURNING row_version
            """),
            {"hacia": hacia, "motivo": motivo, "actor": actor_user_id, "id": dispatch_id},
        )
    ).scalar_one()

    await publicar(
        session,
        aggregate_type="dispatch_request",
        aggregate_id=dispatch_id,
        event_type="dispatch.status_changed",
        payload={
            "desde": solicitud.status,
            "hacia": hacia,
            "company_id": str(solicitud.company_id),
            "actor_user_id": str(actor_user_id),
        },
        dedup_key=f"dispatch:{dispatch_id}:v{nueva_version}",
    )

    # Las cargas siguen a la solicitud. Si esto falla, la transacción revierte
    # el cambio de estado del despacho: nunca quedan desalineados.
    estado_carga = _ESTADO_DE_CARGA.get(hacia)
    if estado_carga is not None:
        permisos_avance = _permisos_de_la_accion(
            permisos, solicitud.company_id, Perm.SHIPMENTS_TRANSITION_FORWARD
        )
        for shipment_id in cargas:
            await _mover_carga(
                session,
                shipment_id=shipment_id,
                hacia=estado_carga,
                actor_user_id=actor_user_id,
                permisos=permisos_avance,
                nota=f"Despacho {solicitud.dispatch_number}",
            )

    if hacia in ESTADOS_FINALES:
        await _liberar_cargas(session, dispatch_id)

    if hacia in {DispatchStatus.REJECTED, DispatchStatus.CANCELLED}:
        # Las cargas vuelven a estar disponibles: regresan a STORED para poder
        # incluirse en otra solicitud. La nota lleva el número de la solicitud
        # para que en la línea de tiempo de la carga se vea la causa.
        permisos_devolucion = _permisos_de_la_accion(
            permisos, solicitud.company_id, Perm.SHIPMENTS_TRANSITION_BACKWARD
        )
        etiqueta = "rechazado" if hacia == DispatchStatus.REJECTED else "cancelado"
        for shipment_id in cargas:
            await _mover_carga(
                session,
                shipment_id=shipment_id,
                hacia=ShipmentStatus.STORED,
                actor_user_id=actor_user_id,
                permisos=permisos_devolucion,
                nota=(
                    f"Despacho {solicitud.dispatch_number} {etiqueta}"
                    + (f": {motivo}" if motivo else "")
                ),
            )

    await _registrar_evento(
        session,
        dispatch_id=dispatch_id,
        tipo=tipo_evento,
        desde=desde,
        hacia=hacia,
        actor_user_id=actor_user_id,
        notas=motivo,
    )

    return ResultadoAccion(id=dispatch_id, desde=desde, hacia=hacia, row_version=nueva_version)


async def _cargas_activas(session: AsyncSession, dispatch_id: UUID) -> list[UUID]:
    return list(
        (
            await session.execute(
                text("""
                    SELECT shipment_id FROM dispatch_request_shipments
                    WHERE dispatch_request_id = :d AND released_at IS NULL
                    ORDER BY shipment_id
                """),
                {"d": dispatch_id},
            )
        )
        .scalars()
        .all()
    )


async def _liberar_cargas(session: AsyncSession, dispatch_id: UUID) -> None:
    """Suelta las cargas para que otra solicitud pueda tomarlas."""
    await session.execute(
        text("""
            UPDATE dispatch_request_shipments
            SET released_at = now()
            WHERE dispatch_request_id = :d AND released_at IS NULL
        """),
        {"d": dispatch_id},
    )


async def _registrar_evento(
    session: AsyncSession,
    *,
    dispatch_id: UUID,
    tipo: DispatchEventType,
    desde: str | None,
    hacia: str | None,
    actor_user_id: UUID,
    notas: str | None = None,
    datos: dict[str, object] | None = None,
) -> None:
    await session.execute(
        text("""
            INSERT INTO dispatch_events
                (dispatch_request_id, event_type, from_status, to_status,
                 notes, actor_user_id, occurred_at, metadata)
            VALUES (:d, :tipo, :desde, :hacia, :notas, :actor, :ahora,
                    CAST(:datos AS JSONB))
        """),
        {
            "d": dispatch_id,
            "tipo": tipo.value,
            "desde": desde,
            "hacia": hacia,
            "notas": notas,
            "actor": actor_user_id,
            "ahora": datetime.now(UTC),
            "datos": json.dumps(datos or {}, default=str, ensure_ascii=False),
        },
    )


# --- Acciones ---


async def aprobar(
    session: AsyncSession,
    *,
    dispatch_id: UUID,
    actor_user_id: UUID,
    permisos: PermisosEfectivos,
    company_ids: list[UUID] | None,
    notas: str | None = None,
    row_version: int | None = None,
) -> ResultadoAccion:
    return await _cambiar_estado(
        session,
        dispatch_id=dispatch_id,
        hacia=DispatchStatus.APPROVED.value,
        actor_user_id=actor_user_id,
        permisos=permisos,
        company_ids=company_ids,
        tipo_evento=DispatchEventType.APPROVED,
        motivo=notas,
        row_version=row_version,
    )


async def rechazar(
    session: AsyncSession,
    *,
    dispatch_id: UUID,
    actor_user_id: UUID,
    permisos: PermisosEfectivos,
    company_ids: list[UUID] | None,
    motivo: str,
    row_version: int | None = None,
) -> ResultadoAccion:
    return await _cambiar_estado(
        session,
        dispatch_id=dispatch_id,
        hacia=DispatchStatus.REJECTED.value,
        actor_user_id=actor_user_id,
        permisos=permisos,
        company_ids=company_ids,
        tipo_evento=DispatchEventType.REJECTED,
        motivo=motivo,
        row_version=row_version,
    )


async def preparar(
    session: AsyncSession,
    *,
    dispatch_id: UUID,
    actor_user_id: UUID,
    permisos: PermisosEfectivos,
    company_ids: list[UUID] | None,
    notas: str | None = None,
    row_version: int | None = None,
) -> ResultadoAccion:
    return await _cambiar_estado(
        session,
        dispatch_id=dispatch_id,
        hacia=DispatchStatus.PREPARING.value,
        actor_user_id=actor_user_id,
        permisos=permisos,
        company_ids=company_ids,
        tipo_evento=DispatchEventType.PREPARING,
        motivo=notas,
        row_version=row_version,
    )


async def completar(
    session: AsyncSession,
    *,
    dispatch_id: UUID,
    actor_user_id: UUID,
    permisos: PermisosEfectivos,
    company_ids: list[UUID] | None,
    notas: str | None = None,
    row_version: int | None = None,
) -> ResultadoAccion:
    """Cierra la solicitud y despacha las cargas.

    Las cargas pasan a `DISPATCHED` y se liberan: el despacho terminó, así que
    ya no las retiene.
    """
    return await _cambiar_estado(
        session,
        dispatch_id=dispatch_id,
        hacia=DispatchStatus.COMPLETED.value,
        actor_user_id=actor_user_id,
        permisos=permisos,
        company_ids=company_ids,
        tipo_evento=DispatchEventType.COMPLETED,
        motivo=notas,
        row_version=row_version,
    )


async def cancelar(
    session: AsyncSession,
    *,
    dispatch_id: UUID,
    actor_user_id: UUID,
    permisos: PermisosEfectivos,
    company_ids: list[UUID] | None,
    motivo: str | None = None,
    row_version: int | None = None,
) -> ResultadoAccion:
    """Cancela la solicitud y devuelve sus cargas a STORED.

    `company_ids is None` identifica a Operaciones (alcance global), que puede
    cancelar hasta `PREPARING`. Un cliente solo antes de la aprobación
    (ADR-0013).
    """
    solicitud = await _cargar_solicitud(session, dispatch_id, company_ids)
    es_operaciones = company_ids is None

    permitidos = _CANCELABLE_POR_OPERACIONES if es_operaciones else _CANCELABLE_POR_CLIENTE
    if solicitud.status not in permitidos:
        if not es_operaciones and solicitud.status in _CANCELABLE_POR_OPERACIONES:
            raise CancelacionNoPermitida(
                "La solicitud ya fue aprobada y no se puede cancelar desde el portal. "
                "Comunicate con Operaciones."
            )
        raise TransicionDeDespachoInvalida(
            f"Una solicitud en {solicitud.status} no se puede cancelar.",
            details=[{"from": solicitud.status}],
        )

    return await _cambiar_estado(
        session,
        dispatch_id=dispatch_id,
        hacia=DispatchStatus.CANCELLED.value,
        actor_user_id=actor_user_id,
        permisos=permisos,
        company_ids=company_ids,
        tipo_evento=DispatchEventType.CANCELLED,
        motivo=motivo,
        row_version=row_version,
    )

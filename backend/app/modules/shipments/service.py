"""Motor de transiciones y requisitos.

El estado de una carga cambia SOLO por aquí. Toda transición valida contra el
catálogo, verifica permiso y alcance, aplica las políticas de dominio, y deja
evento y auditoría — todo en una transacción. Si algo falla, no queda estado
cambiado sin su rastro.
"""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflicto, RecursoNoEncontrado, ReglaDeNegocioViolada, SinPermiso
from app.modules.audit.outbox import publicar
from app.modules.audit.service import registrar
from app.modules.rbac.catalog import Perm
from app.modules.rbac.service import PermisosEfectivos
from app.modules.shipments.models import (
    DisputeStatus,
    EventType,
    RequirementStatus,
    RequirementType,
    ShipmentStatus,
)
from app.modules.shipments.policies import validar_wr_presente_para_almacenar


class TransicionInvalida(Conflicto):
    code = "SHIPMENT_TRANSITION_INVALID"


class VersionDesactualizada(Conflicto):
    code = "SHIPMENT_VERSION_CONFLICT"


class MotivoRequerido(ReglaDeNegocioViolada):
    code = "TRANSICION_EXIGE_MOTIVO"


class RequisitosPendientes(Conflicto):
    code = "SHIPMENT_REQUIREMENTS_PENDING"


class SinPermisoSobreRequisito(Exception):
    """El actor no puede tocar este requisito.

    Igual que `SinPermisoParaTransicion`, la traduce el router: responde 404
    para no confirmar que la carga existe.
    """


class SinPermisoParaTransicion(Exception):
    """El actor no tiene el permiso que exige esta transición.

    No hereda de `ErrorDeAplicacion`: el router decide si responder 403 o 404
    según el caso, y de paso registra el intento denegado.
    """


# A qué columna de fecha corresponde cada estado alcanzado. Se llena en la
# misma transacción que la transición para que el expediente y su cronología
# no puedan divergir.
# Estados al entrar a los cuales se abren los requisitos del catálogo.
_SINCRONIZAN_REQUISITOS: frozenset[str] = frozenset(
    {ShipmentStatus.RECEIVED, ShipmentStatus.STORED}
)

_COLUMNA_DE_FECHA: dict[str, str] = {
    ShipmentStatus.RECEIVED: "received_at",
    ShipmentStatus.STORED: "stored_at",
    ShipmentStatus.DISPATCHED: "dispatched_at",
    ShipmentStatus.DELIVERED: "delivered_at",
}

# ADR-0007: retención operativa de 6 meses desde que la carga llega a
# cualquiera de estos dos estados finales.
_DISPARA_RETENCION: frozenset[str] = frozenset({ShipmentStatus.DELIVERED, ShipmentStatus.CANCELLED})


@dataclass(frozen=True)
class CargaBloqueada:
    """Lo mínimo del shipment que el motor necesita, ya tipado.

    El SELECT crudo devuelve `Any`; empaquetarlo aquí hace que el resto del
    módulo tenga tipos reales.
    """

    id: UUID
    company_id: UUID
    current_status_code: str
    row_version: int


@dataclass(frozen=True)
class ResultadoTransicion:
    shipment_id: UUID
    desde: str
    hacia: str
    row_version: int
    evento_id: UUID


@dataclass(frozen=True)
class DatosTransicion:
    to_status: str
    row_version: int
    occurred_at: datetime | None = None
    note: str | None = None
    location: str | None = None
    metadatos: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class TransicionDisponible:
    to_status: str
    label: str
    requires_reason: bool
    blocked: bool
    blockers: list[dict[str, object]]


async def _cargar_para_actualizar(session: AsyncSession, shipment_id: UUID) -> CargaBloqueada:
    """Bloquea la fila hasta el fin de la transacción.

    `FOR UPDATE` serializa dos transiciones simultáneas sobre la misma carga:
    la segunda espera y ve el estado ya cambiado, en vez de validar contra uno
    obsoleto.
    """
    fila = (
        await session.execute(
            text("""
                SELECT id, company_id, current_status_code, row_version
                FROM shipments
                WHERE id = :id AND deleted_at IS NULL
                FOR UPDATE
            """),
            {"id": shipment_id},
        )
    ).one_or_none()

    if fila is None:
        raise RecursoNoEncontrado("Carga no encontrada.")

    return CargaBloqueada(
        id=fila.id,
        company_id=fila.company_id,
        current_status_code=fila.current_status_code,
        row_version=fila.row_version,
    )


async def _transiciones_permitidas(session: AsyncSession, desde: str) -> list[str]:
    return list(
        (
            await session.execute(
                text("""
                    SELECT to_status_code FROM shipment_status_transitions
                    WHERE from_status_code = :desde AND is_active
                    ORDER BY to_status_code
                """),
                {"desde": desde},
            )
        )
        .scalars()
        .all()
    )


async def transicionar(
    session: AsyncSession,
    *,
    shipment_id: UUID,
    datos: DatosTransicion,
    actor_user_id: UUID,
    permisos: PermisosEfectivos,
    ip_address: str | None = None,
) -> ResultadoTransicion:
    carga = await _cargar_para_actualizar(session, shipment_id)
    desde = carga.current_status_code

    # 1. ¿La transición existe en el catálogo?
    transicion = (
        await session.execute(
            text("""
                SELECT t.requires_reason, p.code AS permiso
                FROM shipment_status_transitions t
                JOIN permissions p ON p.id = t.required_permission_id
                WHERE t.from_status_code = :desde
                  AND t.to_status_code = :hacia
                  AND t.is_active
            """),
            {"desde": desde, "hacia": datos.to_status},
        )
    ).one_or_none()

    if transicion is None:
        permitidas = await _transiciones_permitidas(session, desde)
        raise TransicionInvalida(
            f"No se puede pasar de {desde} a {datos.to_status}.",
            details=[{"from": desde, "allowed": permitidas}],
        )

    # 2. ¿El actor tiene el permiso, con alcance sobre ESTA empresa?
    if not permisos.permite(transicion.permiso, company_id=carga.company_id):
        raise SinPermisoParaTransicion(transicion.permiso)

    # 3. Motivo obligatorio en retrocesos, cancelación, reapertura y reversión.
    if transicion.requires_reason and not (datos.note or "").strip():
        raise MotivoRequerido(f"Pasar de {desde} a {datos.to_status} exige una justificación.")

    # 4. Bloqueo optimista. Se valida después del catálogo para que un cliente
    #    con la versión vieja reciba primero el error más informativo.
    if carga.row_version != datos.row_version:
        raise VersionDesactualizada(
            "La carga fue modificada por otra persona. Recárguela y reintente.",
            details=[{"row_version_actual": carga.row_version}],
        )

    # 5. Políticas de dominio propias de esta transición.
    await _validar_politicas(session, shipment_id=shipment_id, desde=desde, hacia=datos.to_status)

    occurred_at = datos.occurred_at or datetime.now(UTC)

    # 6. Aplicar. La fecha del hito se graba junto al estado.
    columna_fecha = _COLUMNA_DE_FECHA.get(datos.to_status)
    set_fecha = f", {columna_fecha} = :occurred_at" if columna_fecha else ""

    # ADR-0007: `retention_until` se calcula en la MISMA transacción que la
    # transición a DELIVERED/CANCELLED — nunca al vuelo, para que el barrido
    # de archivado pueda hacer `WHERE retention_until <= now()` barato. Si la
    # carga sale de ahí (revertir una entrega, reabrir una cancelada), deja
    # de estar cerrada y la fecha ya no aplica.
    if datos.to_status in _DISPARA_RETENCION:
        set_retencion = (
            ", retention_until = CAST(:occurred_at AS timestamptz) + interval '6 months'"
        )
    elif desde in _DISPARA_RETENCION:
        set_retencion = ", retention_until = NULL"
    else:
        set_retencion = ""

    nueva_version = (
        await session.execute(
            text(f"""
                UPDATE shipments
                SET current_status_code = :hacia,
                    row_version = row_version + 1,
                    updated_at = now(),
                    current_location = COALESCE(:location, current_location)
                    {set_fecha}
                    {set_retencion}
                WHERE id = :id AND row_version = :version
                RETURNING row_version
            """),  # noqa: S608
            {
                "hacia": datos.to_status,
                "id": shipment_id,
                "version": datos.row_version,
                "location": datos.location,
                "occurred_at": occurred_at,
            },
        )
    ).scalar_one()

    # 7. Evento en la línea de tiempo. Si esto falla, la transacción entera se
    #    revierte: nunca queda un estado cambiado sin su registro.
    evento_id = (
        await session.execute(
            text("""
                INSERT INTO shipment_events
                    (shipment_id, event_type, from_status_code, to_status_code,
                     title, description, location, occurred_at, actor_user_id, metadata)
                VALUES (:s, :tipo, :desde, :hacia, :titulo, :nota, :location,
                        :occurred_at, :actor, CAST(:metadatos AS JSONB))
                RETURNING id
            """),
            {
                "s": shipment_id,
                "tipo": EventType.STATUS_CHANGED.value,
                "desde": desde,
                "hacia": datos.to_status,
                "titulo": f"Estado: {desde} → {datos.to_status}",
                "nota": datos.note,
                "location": datos.location,
                "occurred_at": occurred_at,
                "actor": actor_user_id,
                "metadatos": _json(datos.metadatos),
            },
        )
    ).scalar_one()

    # 8. Evento de outbox, en ESTA transacción (Paso 4.1). Si el commit falla,
    #    no queda ni el cambio de estado ni la notificación pendiente. La clave
    #    de deduplicación usa la versión resultante: es única por cambio, así
    #    que un reintento del endpoint no genera dos avisos.
    await publicar(
        session,
        aggregate_type="shipment",
        aggregate_id=shipment_id,
        event_type="shipment.status_changed",
        payload={"desde": desde, "hacia": datos.to_status, "actor_user_id": str(actor_user_id)},
        dedup_key=f"shipment:{shipment_id}:v{nueva_version}",
    )

    # Vive en el motor y no solo en el router: los estados iniciales, acciones
    # masivas y despachos también pasan por aquí y deben dejar la misma
    # auditoría que una transición HTTP individual.
    await registrar(
        session,
        action="shipment.status.changed",
        resource_type="shipment",
        resource_id=shipment_id,
        actor_user_id=actor_user_id,
        company_id=carga.company_id,
        before_data={"status": desde, "row_version": carga.row_version},
        after_data={"status": datos.to_status, "row_version": nueva_version},
        reason=datos.note,
        ip_address=ip_address,
    )

    # 9. Al recibir y al almacenar, se abren los requisitos documentales que le
    #    tocan a esta carga. Va DESPUÉS de aplicar el estado: la aplicabilidad
    #    de la SLI depende de la bodega, y en `PRE_ALERT` todavía no se sabe.
    if datos.to_status in _SINCRONIZAN_REQUISITOS:
        await sincronizar_requisitos_del_catalogo(
            session, shipment_id=shipment_id, actor_user_id=actor_user_id
        )

    return ResultadoTransicion(
        shipment_id=shipment_id,
        desde=desde,
        hacia=datos.to_status,
        row_version=nueva_version,
        evento_id=evento_id,
    )


async def transiciones_disponibles(
    session: AsyncSession,
    *,
    shipment_id: UUID,
    permisos: PermisosEfectivos,
) -> list[TransicionDisponible]:
    """Destinos autorizados y bloqueos actuales, sin cambiar el agregado."""
    carga = (
        await session.execute(
            text("""
                SELECT id, company_id, current_status_code
                FROM shipments
                WHERE id = :id AND deleted_at IS NULL
            """),
            {"id": shipment_id},
        )
    ).one_or_none()
    if carga is None or not permisos.permite(Perm.SHIPMENTS_READ, company_id=carga.company_id):
        raise RecursoNoEncontrado("Carga no encontrada.")

    filas = (
        await session.execute(
            text("""
                SELECT t.to_status_code, t.requires_reason, p.code AS permiso,
                       st.label
                FROM shipment_status_transitions t
                JOIN permissions p ON p.id = t.required_permission_id
                JOIN shipment_statuses st ON st.code = t.to_status_code
                WHERE t.from_status_code = :desde
                  AND t.is_active AND st.is_active
                ORDER BY st.sort_order
            """),
            {"desde": carga.current_status_code},
        )
    ).all()

    disponibles: list[TransicionDisponible] = []
    for fila in filas:
        if not permisos.permite(fila.permiso, company_id=carga.company_id):
            continue

        bloqueos: list[dict[str, object]] = []
        try:
            await _validar_politicas(
                session,
                shipment_id=shipment_id,
                desde=carga.current_status_code,
                hacia=fila.to_status_code,
            )
        except (ReglaDeNegocioViolada, Conflicto) as error:
            bloqueos = [{"code": error.code, "message": error.message, "details": error.details}]

        disponibles.append(
            TransicionDisponible(
                to_status=fila.to_status_code,
                label=fila.label,
                requires_reason=fila.requires_reason,
                blocked=bool(bloqueos),
                blockers=bloqueos,
            )
        )

    return disponibles


async def _validar_politicas(
    session: AsyncSession, *, shipment_id: UUID, desde: str, hacia: str
) -> None:
    """Reglas que dependen de datos relacionados, no del par de estados."""
    if hacia == ShipmentStatus.STORED:
        # ADR-0005: una bodega que emite WR lo exige antes de almacenar.
        await validar_wr_presente_para_almacenar(session, shipment_id)

    await _validar_requisitos_resueltos(session, [shipment_id], hacia)


# Los requisitos documentales declaran su estado bloqueante en el tipo de
# documento (`required_before_status`); los demás bloquean el despacho. Un
# `COALESCE` los unifica sin agregar una columna que diría lo mismo dos veces.
_ESTADO_BLOQUEADO = "COALESCE(dt.required_before_status, 'DISPATCHED')"

_SQL_REQUISITOS_PENDIENTES = f"""
    SELECT s.shipment_number, r.title, r.status
    FROM shipment_requirements r
    JOIN shipments s ON s.id = r.shipment_id
    LEFT JOIN document_types dt ON dt.id = r.document_type_id
    WHERE r.shipment_id = ANY(:cargas)
      AND r.blocks_dispatch
      AND {_ESTADO_BLOQUEADO} = :hacia
      AND r.status NOT IN
          ('FULFILLED', 'VERIFIED', 'NOT_APPLICABLE', 'WAIVED', 'CANCELLED')
    ORDER BY s.shipment_number, r.created_at
"""  # noqa: S608


async def _validar_requisitos_resueltos(
    session: AsyncSession, shipment_ids: list[UUID], hacia: str
) -> None:
    """No se avanza con requisitos bloqueantes abiertos (ADR-0003).

    Cada requisito bloquea UN estado, no siempre el despacho: la prueba de
    entrega bloquea `DELIVERED` y no tendría sentido que impidiera despachar.

    Recibe una lista porque un despacho valida todas sus cargas a la vez, y
    quien lo pide necesita ver todo lo que falta de una, no el primer faltante
    y otra vuelta.
    """
    pendientes = list(
        (
            await session.execute(
                text(_SQL_REQUISITOS_PENDIENTES), {"cargas": shipment_ids, "hacia": hacia}
            )
        ).all()
    )

    if pendientes:
        raise RequisitosPendientes(
            f"Faltan requisitos obligatorios para pasar a {hacia}.",
            details=[
                {"carga": f.shipment_number, "titulo": f.title, "estado": f.status}
                for f in pendientes
            ],
        )


async def validar_requisitos_de_cargas(
    session: AsyncSession, shipment_ids: list[UUID], hacia: str
) -> None:
    """Igual que la validación interna, expuesta para el módulo de despachos.

    Un despacho comprueba lo mismo ANTES de aprobar, no solo al mover la carga:
    descubrir que falta la factura recién al completar el despacho obliga a
    deshacer trabajo ya coordinado.
    """
    await _validar_requisitos_resueltos(session, shipment_ids, hacia)


# --- Requisitos ---


class TransicionDeRequisitoInvalida(ReglaDeNegocioViolada):
    code = "REQUIREMENT_TRANSITION_INVALID"


ESTADO_INICIAL: dict[str, str] = {
    RequirementType.DOCUMENT: RequirementStatus.PENDING,
    RequirementType.INFORMATION: RequirementStatus.OPEN,
    RequirementType.PAYMENT: RequirementStatus.OPEN,
    RequirementType.ACTION: RequirementStatus.OPEN,
}

# Estados que exigen motivo al aplicarse.
_EXIGEN_MOTIVO: frozenset[str] = frozenset({RequirementStatus.WAIVED, RequirementStatus.REJECTED})


async def _empresa_de_la_carga(session: AsyncSession, shipment_id: UUID) -> UUID:
    company_id = (
        await session.execute(
            text("SELECT company_id FROM shipments WHERE id = :s"), {"s": shipment_id}
        )
    ).scalar_one_or_none()

    if company_id is None:
        raise RecursoNoEncontrado("Carga no encontrada.")

    empresa: UUID = company_id
    return empresa


async def abrir_requisito(
    session: AsyncSession,
    *,
    shipment_id: UUID,
    requirement_type: str,
    title: str,
    required_from: str,
    actor_user_id: UUID,
    permisos: PermisosEfectivos,
    document_type_id: UUID | None = None,
    description: str | None = None,
    blocks_dispatch: bool = True,
    due_at: datetime | None = None,
) -> UUID:
    # Sin esto, cualquier usuario autenticado podía abrir requisitos en cargas
    # de otra empresa: el endpoint solo exigía estar logueado.
    company_id = await _empresa_de_la_carga(session, shipment_id)
    if not permisos.permite(Perm.SHIPMENTS_REQUIREMENT_MANAGE, company_id=company_id):
        raise SinPermisoSobreRequisito(Perm.SHIPMENTS_REQUIREMENT_MANAGE)

    requirement_id: UUID = (
        await session.execute(
            text("""
                INSERT INTO shipment_requirements
                    (shipment_id, requirement_type, document_type_id, title, description,
                     required_from, status, blocks_dispatch, due_at, created_by)
                VALUES (:s, :tipo, :doc_tipo, :titulo, :descripcion,
                        :required_from, :estado, :bloquea, :due_at, :actor)
                RETURNING id
            """),
            {
                "s": shipment_id,
                "tipo": requirement_type,
                "doc_tipo": document_type_id,
                "titulo": title,
                "descripcion": description,
                "required_from": required_from,
                "estado": ESTADO_INICIAL[requirement_type],
                "bloquea": blocks_dispatch,
                "due_at": due_at,
                "actor": actor_user_id,
            },
        )
    ).scalar_one()

    await session.execute(
        text("""
            INSERT INTO shipment_events
                (shipment_id, event_type, title, description, occurred_at, actor_user_id)
            VALUES (:s, :tipo, :titulo, :descripcion, now(), :actor)
        """),
        {
            "s": shipment_id,
            "tipo": EventType.REQUIREMENT_OPENED.value,
            "titulo": f"Requisito abierto: {title}",
            "descripcion": description,
            "actor": actor_user_id,
        },
    )

    return requirement_id


async def resolver_requisito(
    session: AsyncSession,
    *,
    requirement_id: UUID,
    nuevo_estado: str,
    actor_user_id: UUID,
    permisos: PermisosEfectivos,
    motivo: str | None = None,
) -> UUID:
    """Cambia el estado de un requisito. Devuelve el `shipment_id`."""
    fila = (
        await session.execute(
            text("""
                SELECT r.shipment_id, r.requirement_type, r.status, r.title,
                       s.company_id
                FROM shipment_requirements r
                JOIN shipments s ON s.id = r.shipment_id
                WHERE r.id = :id
                FOR UPDATE OF r
            """),
            {"id": requirement_id},
        )
    ).one_or_none()

    if fila is None:
        raise RecursoNoEncontrado("Requisito no encontrado.")

    if fila.requirement_type == RequirementType.DOCUMENT and nuevo_estado in {
        RequirementStatus.VERIFIED,
        RequirementStatus.REJECTED,
    }:
        raise TransicionDeRequisitoInvalida(
            "Los requisitos documentales se verifican o rechazan seleccionando el archivo revisado."
        )

    # Exonerar deja avanzar la carga SIN el documento obligatorio, así que pide
    # su propio permiso: `manage` no alcanza (solo OPS_ADMIN y SUPER_ADMIN).
    requerido = (
        Perm.SHIPMENTS_REQUIREMENT_WAIVE
        if nuevo_estado == RequirementStatus.WAIVED
        else Perm.SHIPMENTS_REQUIREMENT_MANAGE
    )
    if not permisos.permite(requerido, company_id=fila.company_id):
        raise SinPermisoSobreRequisito(requerido)

    if fila.status in {RequirementStatus.CANCELLED, RequirementStatus.WAIVED}:
        raise TransicionDeRequisitoInvalida(
            f"El requisito ya está {fila.status} y no admite más cambios."
        )

    if nuevo_estado in _EXIGEN_MOTIVO and not (motivo or "").strip():
        raise MotivoRequerido(f"Pasar el requisito a {nuevo_estado} exige una justificación.")

    resuelto = nuevo_estado in {
        RequirementStatus.FULFILLED,
        RequirementStatus.VERIFIED,
        RequirementStatus.NOT_APPLICABLE,
        RequirementStatus.WAIVED,
        RequirementStatus.CANCELLED,
    }

    await session.execute(
        text("""
            UPDATE shipment_requirements
            SET status = :estado,
                resolution_reason = COALESCE(:motivo, resolution_reason),
                completed_by = CASE WHEN :resuelto THEN :actor ELSE completed_by END,
                completed_at = CASE WHEN :resuelto THEN now() ELSE completed_at END
            WHERE id = :id
        """),
        {
            "estado": nuevo_estado,
            "motivo": motivo,
            "resuelto": resuelto,
            "actor": actor_user_id,
            "id": requirement_id,
        },
    )

    if resuelto:
        await session.execute(
            text("""
                INSERT INTO shipment_events
                    (shipment_id, event_type, title, description, occurred_at, actor_user_id)
                VALUES (:s, :tipo, :titulo, :descripcion, now(), :actor)
            """),
            {
                "s": fila.shipment_id,
                "tipo": EventType.REQUIREMENT_FULFILLED.value,
                "titulo": f"Requisito {nuevo_estado}: {fila.title}",
                "descripcion": motivo,
                "actor": actor_user_id,
            },
        )

    shipment_id: UUID = fila.shipment_id
    return shipment_id


async def reportar_inconformidad(
    session: AsyncSession,
    *,
    shipment_id: UUID,
    reason: str,
    actor_user_id: UUID,
    permisos: PermisosEfectivos,
) -> UUID:
    """Cliente reporta que no reconoce una entrega ya marcada (ADR-0006).

    NO cambia el estado por sí sola — eso lo decide Operaciones al resolver
    (`resolver_inconformidad`). Mientras está `OPEN`, bloquea el archivado de
    la carga (ADR-0007, `shipments/queries.py::candidatas_a_archivar`).
    """
    fila = (
        await session.execute(
            text("""
                SELECT company_id, current_status_code
                FROM shipments WHERE id = :s AND deleted_at IS NULL
                FOR UPDATE
            """),
            {"s": shipment_id},
        )
    ).one_or_none()

    if fila is None:
        raise RecursoNoEncontrado("Carga no encontrada.")

    if not permisos.permite(Perm.SHIPMENTS_DISPUTE_CREATE, company_id=fila.company_id):
        raise SinPermiso(Perm.SHIPMENTS_DISPUTE_CREATE)

    if fila.current_status_code != ShipmentStatus.DELIVERED:
        raise ReglaDeNegocioViolada(
            "Solo se puede reportar una inconformidad sobre una carga ya entregada."
        )

    if not reason.strip():
        raise MotivoRequerido("Contá por qué no reconocés la entrega.")

    try:
        dispute_id: UUID = (
            await session.execute(
                text("""
                    INSERT INTO delivery_disputes (shipment_id, raised_by_user_id, reason)
                    VALUES (:s, :actor, :reason)
                    RETURNING id
                """),
                {"s": shipment_id, "actor": actor_user_id, "reason": reason.strip()},
            )
        ).scalar_one()
    except IntegrityError as error:
        # El índice único parcial (`ix_delivery_disputes_una_abierta_por_carga`)
        # es la garantía real — esto solo la traduce a un error legible.
        raise Conflicto("Ya hay una inconformidad abierta para esta carga.") from error

    await session.execute(
        text("""
            INSERT INTO shipment_events
                (shipment_id, event_type, title, description, occurred_at, actor_user_id)
            VALUES (:s, :tipo, :titulo, :descripcion, now(), :actor)
        """),
        {
            "s": shipment_id,
            "tipo": EventType.DELIVERY_DISPUTED.value,
            "titulo": "Inconformidad de entrega reportada",
            "descripcion": reason.strip(),
            "actor": actor_user_id,
        },
    )

    return dispute_id


async def resolver_inconformidad(
    session: AsyncSession,
    *,
    dispute_id: UUID,
    nuevo_estado: str,
    actor_user_id: UUID,
    permisos: PermisosEfectivos,
    motivo: str | None = None,
    ip_address: str | None = None,
) -> UUID:
    """Resuelve una inconformidad de entrega. Devuelve el `shipment_id`.

    `RESOLVED_REVERTED` dispara la reversión real vía `transicionar()` — el
    mismo motor de transiciones que cualquier otro cambio de estado, con su
    propia revalidación de permiso (`SHIPMENTS_TRANSITION_REVERT_DELIVERED`,
    exclusivo de `SUPER_ADMIN` — esta decisión no cambia esa regla, la
    reafirma) y su propio evento. No se duplica esa lógica acá.
    """
    fila = (
        await session.execute(
            text("""
                SELECT d.shipment_id, d.status, s.company_id, s.row_version
                FROM delivery_disputes d
                JOIN shipments s ON s.id = d.shipment_id
                WHERE d.id = :id
                FOR UPDATE OF d
            """),
            {"id": dispute_id},
        )
    ).one_or_none()

    if fila is None:
        raise RecursoNoEncontrado("Inconformidad no encontrada.")

    if not permisos.permite(Perm.SHIPMENTS_DISPUTE_RESOLVE, company_id=fila.company_id):
        raise SinPermiso(Perm.SHIPMENTS_DISPUTE_RESOLVE)

    if fila.status != DisputeStatus.OPEN:
        raise Conflicto(f"La inconformidad ya está {fila.status} y no admite más cambios.")

    if nuevo_estado == DisputeStatus.RESOLVED_REVERTED:
        if not (motivo or "").strip():
            raise MotivoRequerido("Revertir la entrega exige una justificación.")
        # `transicionar` valida por su cuenta que la carga siga en DELIVERED
        # (rechaza cualquier otro origen) y revalida el permiso — no hace
        # falta duplicar ninguno de los dos chequeos acá.
        await transicionar(
            session,
            shipment_id=fila.shipment_id,
            datos=DatosTransicion(
                to_status=ShipmentStatus.DISPATCHED, row_version=fila.row_version, note=motivo
            ),
            actor_user_id=actor_user_id,
            permisos=permisos,
            ip_address=ip_address,
        )
    elif nuevo_estado != DisputeStatus.RESOLVED_CONFIRMED:
        raise ReglaDeNegocioViolada(f"Estado de resolución inválido: {nuevo_estado}.")

    await session.execute(
        text("""
            UPDATE delivery_disputes
            SET status = :estado, resolved_at = now(), resolved_by_user_id = :actor
            WHERE id = :id
        """),
        {"estado": nuevo_estado, "actor": actor_user_id, "id": dispute_id},
    )

    await session.execute(
        text("""
            INSERT INTO shipment_events
                (shipment_id, event_type, title, description, occurred_at, actor_user_id)
            VALUES (:s, :tipo, :titulo, :descripcion, now(), :actor)
        """),
        {
            "s": fila.shipment_id,
            "tipo": EventType.DELIVERY_DISPUTE_RESOLVED.value,
            "titulo": f"Inconformidad resuelta: {nuevo_estado}",
            "descripcion": motivo,
            "actor": actor_user_id,
        },
    )

    shipment_id: UUID = fila.shipment_id
    return shipment_id


async def archivar_pendientes(session: AsyncSession, *, limite: int = 500) -> int:
    """Barrido de archivado (ADR-0007): retención operativa cumplida, sin
    bloqueos. Devuelve cuántas cargas archivó.

    No borra nada — `archived_at IS NOT NULL` es lo único que cambia, y toda
    consulta operativa ya filtra por eso desde su diseño original (Paso 2.5).
    La recompresión de los documentos de cada carga archivada es un paso
    aparte: que falle ahí no puede impedirle a esta carga salir del flujo
    activo a tiempo.

    `FOR UPDATE SKIP LOCKED`: si dos workers corrieran a la vez, cada uno
    toma cargas distintas en vez de esperarse o pisarse.
    """
    filas = list(
        (
            await session.execute(
                text("""
                    SELECT id, company_id FROM shipments
                    WHERE current_status_code IN ('DELIVERED', 'CANCELLED')
                      AND retention_until <= now()
                      AND archived_at IS NULL
                      AND legal_hold = false
                      AND legacy_review_required = false
                      AND NOT EXISTS (
                          SELECT 1 FROM delivery_disputes d
                          WHERE d.shipment_id = shipments.id AND d.status = 'OPEN'
                      )
                    ORDER BY retention_until
                    LIMIT :limite
                    FOR UPDATE SKIP LOCKED
                """),
                {"limite": limite},
            )
        ).all()
    )

    for fila in filas:
        await session.execute(
            text("UPDATE shipments SET archived_at = now() WHERE id = :id"), {"id": fila.id}
        )
        await registrar(
            session,
            action="shipment.archived",
            resource_type="shipment",
            resource_id=fila.id,
            company_id=fila.company_id,
            # `actor_user_id=None`: lo ejecutó el barrido, no una persona.
        )

    return len(filas)


class EvidenciaDocumentalInvalida(ReglaDeNegocioViolada):
    code = "REQUIREMENT_DOCUMENT_INVALID"


async def _requisito_y_documento_para_revision(
    session: AsyncSession,
    *,
    shipment_id: UUID,
    requirement_id: UUID,
    document_id: UUID,
) -> Any:
    fila = (
        await session.execute(
            text("""
                SELECT r.id, r.shipment_id, r.requirement_type, r.document_type_id,
                       r.status, r.title, s.company_id,
                       d.id AS document_id, d.upload_status, d.deleted_at
                FROM shipment_requirements r
                JOIN shipments s ON s.id = r.shipment_id
                LEFT JOIN shipment_documents sd
                  ON sd.shipment_id = r.shipment_id
                 AND sd.document_type_id = r.document_type_id
                 AND sd.document_id = :document_id
                LEFT JOIN documents d ON d.id = sd.document_id
                WHERE r.id = :requirement_id AND r.shipment_id = :shipment_id
                FOR UPDATE OF r
            """),
            {
                "shipment_id": shipment_id,
                "requirement_id": requirement_id,
                "document_id": document_id,
            },
        )
    ).one_or_none()

    if fila is None:
        raise RecursoNoEncontrado("Requisito no encontrado.")
    if fila.requirement_type != RequirementType.DOCUMENT or fila.document_type_id is None:
        raise EvidenciaDocumentalInvalida("El requisito no es documental.")
    if fila.document_id is None or fila.upload_status != "READY" or fila.deleted_at is not None:
        raise EvidenciaDocumentalInvalida(
            "Seleccione un documento listo, vigente y del mismo tipo que el requisito."
        )
    return fila


async def verificar_requisito_documental(
    session: AsyncSession,
    *,
    shipment_id: UUID,
    requirement_id: UUID,
    document_id: UUID,
    actor_user_id: UUID,
    permisos: PermisosEfectivos,
    nota: str | None = None,
) -> None:
    fila = await _requisito_y_documento_para_revision(
        session,
        shipment_id=shipment_id,
        requirement_id=requirement_id,
        document_id=document_id,
    )
    if not permisos.permite(Perm.DOCUMENTS_VERIFY, company_id=fila.company_id):
        raise SinPermisoSobreRequisito(Perm.DOCUMENTS_VERIFY)
    if fila.status != RequirementStatus.UPLOADED:
        raise TransicionDeRequisitoInvalida(
            f"El requisito está {fila.status}; solo un documento subido puede verificarse."
        )

    await session.execute(
        text("""
            UPDATE shipment_requirements
            SET status = 'VERIFIED', verified_document_id = :document_id,
                reviewed_document_id = :document_id, resolution_reason = :nota,
                completed_by = :actor, completed_at = now()
            WHERE id = :id
        """),
        {
            "document_id": document_id,
            "nota": (nota or "").strip() or None,
            "actor": actor_user_id,
            "id": requirement_id,
        },
    )
    await _evento_revision_documental(
        session,
        fila=fila,
        estado=RequirementStatus.VERIFIED.value,
        actor_user_id=actor_user_id,
        descripcion=nota,
    )


async def rechazar_requisito_documental(
    session: AsyncSession,
    *,
    shipment_id: UUID,
    requirement_id: UUID,
    document_id: UUID,
    actor_user_id: UUID,
    permisos: PermisosEfectivos,
    motivo: str,
) -> None:
    if not motivo.strip():
        raise MotivoRequerido("Rechazar un documento exige una justificación.")
    fila = await _requisito_y_documento_para_revision(
        session,
        shipment_id=shipment_id,
        requirement_id=requirement_id,
        document_id=document_id,
    )
    if not permisos.permite(Perm.DOCUMENTS_VERIFY, company_id=fila.company_id):
        raise SinPermisoSobreRequisito(Perm.DOCUMENTS_VERIFY)
    if fila.status != RequirementStatus.UPLOADED:
        raise TransicionDeRequisitoInvalida(
            f"El requisito está {fila.status}; solo un documento subido puede rechazarse."
        )

    await session.execute(
        text("""
            UPDATE shipment_requirements
            SET status = 'REJECTED', verified_document_id = NULL,
                reviewed_document_id = :document_id, resolution_reason = :motivo,
                completed_by = NULL, completed_at = NULL
            WHERE id = :id
        """),
        {
            "document_id": document_id,
            "motivo": motivo.strip(),
            "id": requirement_id,
        },
    )
    await _evento_revision_documental(
        session,
        fila=fila,
        estado=RequirementStatus.REJECTED.value,
        actor_user_id=actor_user_id,
        descripcion=motivo,
    )
    await publicar(
        session,
        aggregate_type="shipment",
        aggregate_id=shipment_id,
        event_type="shipment.document_rejected",
        payload={
            "requirement_id": str(requirement_id),
            "document_id": str(document_id),
            "motivo": motivo.strip(),
        },
        dedup_key=f"requirement:{requirement_id}:rejected:{document_id}",
    )


async def _evento_revision_documental(
    session: AsyncSession,
    *,
    fila: Any,
    estado: str,
    actor_user_id: UUID,
    descripcion: str | None,
) -> None:
    await session.execute(
        text("""
            INSERT INTO shipment_events
                (shipment_id, event_type, title, description, occurred_at, actor_user_id)
            VALUES (:s, :tipo, :titulo, :descripcion, now(), :actor)
        """),
        {
            "s": fila.shipment_id,
            "tipo": EventType.REQUIREMENT_FULFILLED.value,
            "titulo": f"Documento {estado}: {fila.title}",
            "descripcion": (descripcion or "").strip() or None,
            "actor": actor_user_id,
        },
    )


def _json(datos: dict[str, object]) -> str:
    return json.dumps(datos, default=str, ensure_ascii=False)


# --- Requisitos que nacen del catálogo (Paso 0.4 / ADR-0003) ---

# Cuándo aplica cada tipo de documento a una carga concreta. Los que no están
# acá no se abren solos: el BL no bloquea nada (ADR-0006) y el permiso especial
# depende de que Operaciones marque la carga para revisión.
_APLICABILIDAD: dict[str, str] = {
    "COMMERCIAL_INVOICE": "true",
    "PACKING_LIST": "true",
    "PROOF_OF_DELIVERY": "true",
    # ADR-0005: la SLI la exige la bodega de origen, no la ciudad.
    "SLI": "COALESCE(f.uses_warehouse_receipt, false)",
    # Decisión #3: el cliente no puede descartar la advertencia, pero tampoco
    # se le pide el permiso si nadie determinó que la mercancía lo necesita.
    "SPECIAL_PERMIT": "s.permit_review_required",
}

_SQL_SINCRONIZAR_REQUISITOS = f"""
    INSERT INTO shipment_requirements
        (shipment_id, requirement_type, document_type_id, title, description,
         required_from, status, blocks_dispatch, created_by)
    SELECT s.id, 'DOCUMENT', dt.id, dt.label, dt.description,
           CASE WHEN dt.provided_by = 'STAFF' THEN 'STAFF' ELSE 'CLIENT' END,
           'PENDING', true, :actor
    FROM shipments s
    LEFT JOIN facilities f ON f.id = s.origin_facility_id
    JOIN document_types dt ON dt.is_active
                          AND dt.context = 'SHIPMENT'
                          AND dt.required_before_status IS NOT NULL
    WHERE s.id = :shipment_id
      AND CASE dt.code
            {"".join(f"WHEN '{codigo}' THEN {condicion} " for codigo, condicion in _APLICABILIDAD.items())}
            ELSE false
          END
      -- Idempotente: no reabre lo que ya existe, ni siquiera si fue exonerado
      -- o rechazado. Reabrirlo borraría la decisión de Operaciones.
      AND NOT EXISTS (
          SELECT 1 FROM shipment_requirements existente
          WHERE existente.shipment_id = s.id
            AND existente.document_type_id = dt.id
      )
    RETURNING id, title
"""  # noqa: S608


async def sincronizar_requisitos_del_catalogo(
    session: AsyncSession, *, shipment_id: UUID, actor_user_id: UUID
) -> list[UUID]:
    """Abre los requisitos documentales que le corresponden a esta carga.

    Sin esto, `document_types.required_before_status` sería configuración
    muerta: nadie abriría los requisitos y "documento obligatorio" no obligaría
    a nada.

    Se llama desde el motor de transiciones al entrar a `RECEIVED` y a `STORED`,
    no antes: la SLI depende de la bodega de origen, que hasta que la carga no
    se recibe puede no estar definida. Es idempotente, así que llamarlo de más
    no duplica nada.
    """
    abiertos = list(
        (
            await session.execute(
                text(_SQL_SINCRONIZAR_REQUISITOS),
                {"shipment_id": shipment_id, "actor": actor_user_id},
            )
        ).all()
    )

    for fila in abiertos:
        await session.execute(
            text("""
                INSERT INTO shipment_events
                    (shipment_id, event_type, title, description, occurred_at, actor_user_id)
                VALUES (:s, :tipo, :titulo, :descripcion, now(), :actor)
            """),
            {
                "s": shipment_id,
                "tipo": EventType.REQUIREMENT_OPENED.value,
                "titulo": f"Requisito abierto: {fila.title}",
                "descripcion": "Exigido por el catálogo de documentos.",
                "actor": actor_user_id,
            },
        )

    return [fila.id for fila in abiertos]

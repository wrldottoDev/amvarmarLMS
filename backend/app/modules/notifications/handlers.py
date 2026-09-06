"""Manejadores del outbox: de evento de negocio a notificación (Paso 4.2).

Traducen lo que publicó el Paso 4.1 en avisos concretos. Toda la lógica de a
quién avisar vive acá, no en el servicio que produce el evento: el motor de
transiciones no tiene por qué saber quién quiere enterarse.

Los manejadores toleran recibir el mismo evento dos veces (ADR-0014). La
defensa es `dedup_key`, que se deriva del propio evento del outbox y por lo
tanto es estable entre reintentos.
"""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.outbox import EventoPendiente
from app.modules.dispatches.models import DispatchStatus
from app.modules.notifications import service
from app.modules.shipments.models import ShipmentStatus

# Estados de carga que ADR-0008 marca como aviso crítico propio. El resto cae
# en `shipment.status_changed`, que es el avance rutinario.
_EVENTO_POR_ESTADO: dict[str, str] = {
    ShipmentStatus.STORED: "shipment.stored",
    ShipmentStatus.DISPATCHED: "shipment.dispatched",
    ShipmentStatus.DELIVERED: "shipment.delivered",
}

# Retrocesos, cancelación, reapertura y reversión son "correcciones
# importantes" (evento crítico 7 de ADR-0008), no avance normal.
_ESTADOS_DE_CORRECCION: frozenset[str] = frozenset(
    {ShipmentStatus.CANCELLED, ShipmentStatus.PRE_ALERT}
)

# Estados de despacho con aviso propio. El BL se notifica por su evento READY,
# no por completar: son hechos distintos y pueden ocurrir en cualquier orden.
_EVENTO_POR_ESTADO_DE_DESPACHO: dict[str, str] = {
    DispatchStatus.APPROVED: "dispatch.approved",
    DispatchStatus.DISPATCHED: "dispatch.dispatched",
    DispatchStatus.COMPLETED: "dispatch.completed",
}


async def _referencia_de_carga(session: AsyncSession, shipment_id: UUID) -> str | None:
    """El identificador que el cliente reconoce: su WR o su número de factura.

    Es lo único de negocio que ADR-0008 deja salir en un correo. Sale de
    `shipment_references`, que guarda ambos tipos, con el WR primero porque es
    el que usa quien despacha desde una bodega que lo emite; el resto de las
    cargas solo tiene factura. `shipment_number` no sirve para esto: es interno
    y el cliente nunca lo vio en un papel.
    """
    referencia: str | None = (
        await session.execute(
            text("""
                SELECT value FROM shipment_references
                WHERE shipment_id = :s AND reference_type IN ('WR', 'INVOICE')
                ORDER BY CASE reference_type WHEN 'WR' THEN 0 ELSE 1 END, created_at
                LIMIT 1
            """),
            {"s": shipment_id},
        )
    ).scalar_one_or_none()
    return referencia


async def _carga(session: AsyncSession, shipment_id: UUID) -> tuple[UUID, str | None] | None:
    """Empresa dueña de la carga y su identificador, o `None` si ya no existe."""
    fila = (
        await session.execute(
            text("SELECT company_id FROM shipments WHERE id = :s"), {"s": shipment_id}
        )
    ).scalar_one_or_none()

    if fila is None:
        return None

    return fila, await _referencia_de_carga(session, shipment_id)


async def _numero_de_solicitud(session: AsyncSession, dispatch_id: UUID) -> str | None:
    numero: str | None = (
        await session.execute(
            text("SELECT dispatch_number FROM dispatch_requests WHERE id = :d"),
            {"d": dispatch_id},
        )
    ).scalar_one_or_none()
    return numero


def _codigo_de_transicion(desde: str, hacia: str) -> str:
    if hacia in _EVENTO_POR_ESTADO:
        return _EVENTO_POR_ESTADO[hacia]

    # Un retroceso se reconoce por el destino, no por comparar posiciones: el
    # catálogo de transiciones ya define cuáles exigen justificación, y esos
    # son los que el cliente necesita ver explicados.
    if hacia in _ESTADOS_DE_CORRECCION:
        return "shipment.corrected"

    return "shipment.status_changed"


async def cambio_de_estado_de_carga(session: AsyncSession, evento: EventoPendiente) -> None:
    carga = await _carga(session, evento.aggregate_id)
    if carga is None:
        # La carga ya no existe. No es un fallo que valga la pena reintentar.
        return

    empresa, referencia = carga

    codigo = _codigo_de_transicion(
        str(evento.payload.get("desde", "")), str(evento.payload.get("hacia", ""))
    )

    await service.notificar(
        session,
        event_code=codigo,
        destinatarios=await service.destinatarios_de_empresa(session, empresa),
        resource_type="shipment",
        resource_id=evento.aggregate_id,
        referencia=referencia,
        # El id del evento del outbox es único por cambio de estado, así que
        # sirve de clave estable: reintentar no genera un segundo aviso.
        dedup_key=f"outbox:{evento.id}",
    )


async def cambio_de_estado_de_despacho(session: AsyncSession, evento: EventoPendiente) -> None:
    empresa_cruda = evento.payload.get("company_id")
    if empresa_cruda is None:
        return

    hacia = str(evento.payload.get("hacia", ""))

    await service.notificar(
        session,
        event_code=_EVENTO_POR_ESTADO_DE_DESPACHO.get(hacia, "dispatch.status_changed"),
        destinatarios=await service.destinatarios_de_empresa(session, UUID(str(empresa_cruda))),
        resource_type="dispatch_request",
        resource_id=evento.aggregate_id,
        referencia=await _numero_de_solicitud(session, evento.aggregate_id),
        dedup_key=f"outbox:{evento.id}",
    )


async def solicitud_de_despacho_creada(session: AsyncSession, evento: EventoPendiente) -> None:
    """Dos avisos de un solo hecho: el acuse al cliente y el pedido a Operaciones.

    En el sistema anterior eran dos funciones separadas
    (`send_dispatch_received_email_to_user` y `send_dispatch_request_email_to_admin`)
    disparadas desde la vista. Acá salen del mismo evento del outbox, así que o
    se mandan los dos o se reintentan los dos: no queda el cliente avisado y
    Operaciones sin enterarse.

    Las `dedup_key` llevan sufijos distintos porque son avisos distintos. Sin
    eso, el segundo `notificar()` vería la clave del primero y se saltearía el
    correo para cualquiera que reciba ambos — que es justo el caso de un
    `SUPER_ADMIN` con membresía de la empresa.
    """
    empresa_cruda = evento.payload.get("company_id")
    if empresa_cruda is None:
        return

    referencia = await _numero_de_solicitud(session, evento.aggregate_id)

    await service.notificar(
        session,
        event_code="dispatch.requested",
        destinatarios=await service.destinatarios_de_empresa(session, UUID(str(empresa_cruda))),
        resource_type="dispatch_request",
        resource_id=evento.aggregate_id,
        referencia=referencia,
        dedup_key=f"outbox:{evento.id}:cliente",
    )

    await service.notificar(
        session,
        event_code="dispatch.requested_internal",
        destinatarios=await service.destinatarios_de_operaciones(session),
        resource_type="dispatch_request",
        resource_id=evento.aggregate_id,
        referencia=referencia,
        dedup_key=f"outbox:{evento.id}:interno",
    )


async def documento_de_despacho_listo(session: AsyncSession, evento: EventoPendiente) -> None:
    """Avisa por el BL cuando sus bytes están READY, no por cerrar el despacho."""
    if str(evento.payload.get("document_type_code", "")).upper() != "BL":
        return

    fila = (
        await session.execute(
            text("""
                SELECT dr.id, dr.company_id, dr.dispatch_number
                FROM dispatch_documents dd
                JOIN dispatch_requests dr ON dr.id = dd.dispatch_request_id
                JOIN documents d ON d.id = dd.document_id
                WHERE dd.document_id = :document_id
                  AND d.upload_status = 'READY' AND d.deleted_at IS NULL
            """),
            {"document_id": evento.aggregate_id},
        )
    ).one_or_none()
    if fila is None:
        return

    await service.notificar(
        session,
        event_code="dispatch.bol_available",
        destinatarios=await service.destinatarios_de_empresa(session, fila.company_id),
        resource_type="dispatch_request",
        resource_id=fila.id,
        referencia=fila.dispatch_number,
        dedup_key=f"outbox:{evento.id}",
    )


# El worker (`app.workers.tasks.outbox`) arma su registro a partir de esto. Se
# declara acá, junto a los manejadores, para que agregar un evento nuevo sea
# tocar un solo archivo.
POR_EVENTO = {
    "shipment.status_changed": cambio_de_estado_de_carga,
    "dispatch.status_changed": cambio_de_estado_de_despacho,
    "dispatch.created": solicitud_de_despacho_creada,
    "dispatch.document.ready": documento_de_despacho_listo,
}

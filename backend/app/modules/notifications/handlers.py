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
from app.modules.notifications import service
from app.modules.shipments.models import ShipmentStatus

# Estados de carga que ADR-0008 marca como aviso crítico propio. El resto cae
# en `shipment.status_changed`, que es el avance rutinario.
_EVENTO_POR_ESTADO: dict[str, str] = {
    ShipmentStatus.DISPATCHED: "shipment.dispatched",
    ShipmentStatus.DELIVERED: "shipment.delivered",
}

# Retrocesos, cancelación, reapertura y reversión son "correcciones
# importantes" (evento crítico 7 de ADR-0008), no avance normal.
_ESTADOS_DE_CORRECCION: frozenset[str] = frozenset(
    {ShipmentStatus.CANCELLED, ShipmentStatus.PRE_ALERT}
)


async def _empresa_de_carga(session: AsyncSession, shipment_id: UUID) -> UUID | None:
    empresa: UUID | None = (
        await session.execute(
            text("SELECT company_id FROM shipments WHERE id = :s"), {"s": shipment_id}
        )
    ).scalar_one_or_none()
    return empresa


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
    empresa = await _empresa_de_carga(session, evento.aggregate_id)
    if empresa is None:
        # La carga ya no existe. No es un fallo que valga la pena reintentar.
        return

    codigo = _codigo_de_transicion(
        str(evento.payload.get("desde", "")), str(evento.payload.get("hacia", ""))
    )

    await service.notificar(
        session,
        event_code=codigo,
        destinatarios=await service.destinatarios_de_empresa(session, empresa),
        resource_type="shipment",
        resource_id=evento.aggregate_id,
        # El id del evento del outbox es único por cambio de estado, así que
        # sirve de clave estable: reintentar no genera un segundo aviso.
        dedup_key=f"outbox:{evento.id}",
    )


async def cambio_de_estado_de_despacho(session: AsyncSession, evento: EventoPendiente) -> None:
    empresa_cruda = evento.payload.get("company_id")
    if empresa_cruda is None:
        return

    await service.notificar(
        session,
        event_code="dispatch.status_changed",
        destinatarios=await service.destinatarios_de_empresa(session, UUID(str(empresa_cruda))),
        resource_type="dispatch_request",
        resource_id=evento.aggregate_id,
        dedup_key=f"outbox:{evento.id}",
    )


# El worker (`app.workers.tasks.outbox`) arma su registro a partir de esto. Se
# declara acá, junto a los manejadores, para que agregar un evento nuevo sea
# tocar un solo archivo.
POR_EVENTO = {
    "shipment.status_changed": cambio_de_estado_de_carga,
    "dispatch.status_changed": cambio_de_estado_de_despacho,
}

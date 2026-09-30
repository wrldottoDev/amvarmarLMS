"""Catálogo de estados y transiciones — fuente única de verdad (ADR-0001).

El seed y los tests del grafo leen de aquí. Cambiar una transición se hace en
este archivo y en ADR-0001, en ningún otro lado.
"""

from dataclasses import dataclass
from itertools import pairwise

from app.modules.rbac.catalog import Perm
from app.modules.shipments.models import ShipmentStatus as S
from app.modules.shipments.models import StatusCategory as C


@dataclass(frozen=True)
class DefinicionEstado:
    label: str
    category: C
    sort_order: int
    is_terminal: bool = False


ESTADOS: dict[str, DefinicionEstado] = {
    S.PRE_ALERT: DefinicionEstado("Prealerta", C.PRE_ARRIVAL, 10),
    S.IN_TRANSIT: DefinicionEstado("En tránsito", C.PRE_ARRIVAL, 20),
    S.RECEIVED: DefinicionEstado("Recibida", C.WAREHOUSE, 30),
    S.STORED: DefinicionEstado("Almacenada", C.WAREHOUSE, 40),
    S.DISPATCH_REQUESTED: DefinicionEstado("Despacho solicitado", C.DISPATCH, 50),
    S.PREPARING: DefinicionEstado("En preparación", C.DISPATCH, 60),
    S.DISPATCHED: DefinicionEstado("Despachada", C.DISPATCH, 70),
    S.DELIVERED: DefinicionEstado("Entregada", C.FINAL, 80, is_terminal=True),
    S.CANCELLED: DefinicionEstado("Cancelada", C.FINAL, 90, is_terminal=True),
}


@dataclass(frozen=True)
class DefinicionTransicion:
    desde: str
    hacia: str
    permiso: str
    # ADR-0001: retroceder, cancelar, reabrir y revertir una entrega exigen
    # justificación obligatoria. Avanzar normalmente, no.
    requiere_motivo: bool = False


# El flujo normal. Cada par consecutivo define también su retroceso de un paso.
_FLUJO: tuple[str, ...] = (
    S.PRE_ALERT,
    S.IN_TRANSIT,
    S.RECEIVED,
    S.STORED,
    S.DISPATCH_REQUESTED,
    S.PREPARING,
    S.DISPATCHED,
    S.DELIVERED,
)


def _construir_transiciones() -> tuple[DefinicionTransicion, ...]:
    transiciones: list[DefinicionTransicion] = []

    for desde, hacia in pairwise(_FLUJO):
        transiciones.append(DefinicionTransicion(desde, hacia, Perm.SHIPMENTS_TRANSITION_FORWARD))

        # Retroceso de un paso. Se genera desde el mismo flujo para que agregar
        # un estado no deje su retroceso olvidado.
        if hacia == S.DELIVERED:
            # Revertir una entrega es el caso más sensible: permiso exclusivo
            # de SUPER_ADMIN (ADR-0001).
            transiciones.append(
                DefinicionTransicion(
                    hacia,
                    desde,
                    Perm.SHIPMENTS_TRANSITION_REVERT_DELIVERED,
                    requiere_motivo=True,
                )
            )
        else:
            transiciones.append(
                DefinicionTransicion(
                    hacia, desde, Perm.SHIPMENTS_TRANSITION_BACKWARD, requiere_motivo=True
                )
            )

    # Cancelación: solo desde PRE_ALERT o IN_TRANSIT. Desde RECEIVED en adelante
    # la carga ya existe físicamente y no se cancela — si se cancela el despacho,
    # se modifica `dispatch_requests`, no el estado de la carga.
    transiciones.append(
        DefinicionTransicion(
            S.PRE_ALERT, S.CANCELLED, Perm.SHIPMENTS_CANCEL_PREALERT, requiere_motivo=True
        )
    )
    transiciones.append(
        DefinicionTransicion(
            S.IN_TRANSIT, S.CANCELLED, Perm.SHIPMENTS_CANCEL_IN_TRANSIT, requiere_motivo=True
        )
    )

    # Reapertura: vuelve al estado exacto desde el que se canceló, no siempre a
    # PRE_ALERT (ADR-0001). Por eso hay dos destinos posibles.
    for destino in (S.PRE_ALERT, S.IN_TRANSIT):
        transiciones.append(
            DefinicionTransicion(S.CANCELLED, destino, Perm.SHIPMENTS_REOPEN, requiere_motivo=True)
        )

    return tuple(transiciones)


TRANSICIONES: tuple[DefinicionTransicion, ...] = _construir_transiciones()

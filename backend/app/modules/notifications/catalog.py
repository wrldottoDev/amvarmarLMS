"""Catálogo de eventos notificables (ADR-0008).

`critico` no cambia a quién ni por dónde se avisa hoy: `IN_APP` y `EMAIL` son
obligatorios para todo evento. Importa para WhatsApp, cuando exista: los no
críticos serán los únicos que el usuario podrá apagar uno por uno. Se clasifica
desde ahora para no tener que revisar cien eventos después.

**Regla dura de ADR-0008 sobre el correo:** ni el asunto ni el cuerpo llevan
datos de negocio. Nada de números de carga, nombres de documentos, montos ni
nombres de empresa. Un correo queda en la bandeja de entrada, se reenvía y se
sincroniza a servicios de terceros; el dato concreto vive detrás del login. Por
eso los textos de acá son fijos y el enlace es lo único que apunta al caso.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class DefinicionEvento:
    codigo: str
    critico: bool
    asunto: str
    mensaje: str
    # A qué pantalla lleva el enlace. `{id}` se sustituye con el recurso.
    ruta: str


_ENTRAR = "Entre al sistema para ver el detalle."

EVENTOS: dict[str, DefinicionEvento] = {
    # --- Críticos (los ocho de ADR-0008) ---
    "shipment.requirement_blocking": DefinicionEvento(
        codigo="shipment.requirement_blocking",
        critico=True,
        asunto="Falta documentación para despachar",
        mensaje=f"Una de sus cargas no puede despacharse porque falta un documento obligatorio. {_ENTRAR}",
        ruta="/cargas/{id}",
    ),
    "shipment.requirement_rejected": DefinicionEvento(
        codigo="shipment.requirement_rejected",
        critico=True,
        asunto="Un documento fue rechazado",
        mensaje=f"Se rechazó un documento de una de sus cargas y hay que volver a cargarlo. {_ENTRAR}",
        ruta="/cargas/{id}",
    ),
    "shipment.permit_review": DefinicionEvento(
        codigo="shipment.permit_review",
        critico=True,
        asunto="Su carga podría requerir un permiso especial",
        mensaje=f"Una de sus cargas quedó marcada para revisión de permisos o inspección. {_ENTRAR}",
        ruta="/cargas/{id}",
    ),
    "dispatch.status_changed": DefinicionEvento(
        codigo="dispatch.status_changed",
        critico=True,
        asunto="Actualización de su solicitud de despacho",
        mensaje=f"Una de sus solicitudes de despacho cambió de estado. {_ENTRAR}",
        ruta="/despachos/{id}",
    ),
    "shipment.dispatched": DefinicionEvento(
        codigo="shipment.dispatched",
        critico=True,
        asunto="Su carga fue despachada",
        mensaje=f"Una de sus cargas salió despachada. {_ENTRAR}",
        ruta="/cargas/{id}",
    ),
    "shipment.delivered": DefinicionEvento(
        codigo="shipment.delivered",
        critico=True,
        asunto="Su carga fue entregada",
        mensaje=f"Se registró la entrega de una de sus cargas. {_ENTRAR}",
        ruta="/cargas/{id}",
    ),
    "shipment.corrected": DefinicionEvento(
        codigo="shipment.corrected",
        critico=True,
        asunto="Corrección en el estado de una carga",
        mensaje=f"Se corrigió el estado de una de sus cargas. {_ENTRAR}",
        ruta="/cargas/{id}",
    ),
    "account.security_alert": DefinicionEvento(
        codigo="account.security_alert",
        critico=True,
        asunto="Alerta de seguridad en su cuenta",
        mensaje=(
            "Se detectó actividad relevante para la seguridad de su cuenta. "
            "Si no la reconoce, cambie su contraseña. Entre al sistema para revisarla."
        ),
        ruta="/cuenta/seguridad",
    ),
    # --- No críticos ---
    "shipment.status_changed": DefinicionEvento(
        codigo="shipment.status_changed",
        critico=False,
        asunto="Su carga avanzó de estado",
        mensaje=f"Una de sus cargas cambió de estado. {_ENTRAR}",
        ruta="/cargas/{id}",
    ),
    "document.archiving_soon": DefinicionEvento(
        codigo="document.archiving_soon",
        critico=False,
        asunto="Documentos próximos a archivarse",
        mensaje=(
            "Documentos de una de sus cargas cumplen seis meses y pasarán al historial "
            f"archivado. No se elimina nada. {_ENTRAR}"
        ),
        ruta="/cargas/{id}",
    ),
}


def definicion(codigo: str) -> DefinicionEvento | None:
    return EVENTOS.get(codigo)

"""Catálogo de eventos notificables (ADR-0008).

`critico` no cambia a quién ni por dónde se avisa hoy: `IN_APP` y `EMAIL` son
obligatorios para todo evento. Importa para WhatsApp, cuando exista: los no
críticos serán los únicos que el usuario podrá apagar uno por uno. Se clasifica
desde ahora para no tener que revisar cien eventos después.

**Regla de ADR-0008, con su enmienda del 25-08-2026:** el correo puede llevar el
**identificador** —WR, número de factura, número de solicitud— y el estado al
que cambió. No lleva shipper, carrier, pesos, montos, la lista completa de
cargas de un despacho, ni ningún documento adjunto.

El motivo del corte: el WR y la factura ya están en los papeles del embarque y
el cliente los tiene, así que repetirlos no expone nada que no circule ya por su
bandeja. El transportista, los pesos y la relación comercial son otra cosa, y de
esos sí se aprende algo mirando un buzón ajeno.

Nunca viajan credenciales: para eso está el enlace de invitación.
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
    # Dónde se muestra el identificador. `{ref}` se reemplaza con el WR, la
    # factura o el número de solicitud; si no hay ninguno, la frase se acorta
    # sola en vez de dejar un hueco.
    con_referencia: str | None = None


_ENTRAR = "Entre al sistema para ver el detalle."


EVENTOS: dict[str, DefinicionEvento] = {
    # --- Críticos (los ocho de ADR-0008) ---
    "shipment.requirement_blocking": DefinicionEvento(
        codigo="shipment.requirement_blocking",
        critico=True,
        asunto="Falta documentación para despachar",
        mensaje=f"Una de sus cargas no puede despacharse porque falta un documento obligatorio. {_ENTRAR}",
        con_referencia=f"La carga {{ref}} no puede despacharse porque falta un documento obligatorio. {_ENTRAR}",
        ruta="/shipments/{id}",
    ),
    "shipment.requirement_rejected": DefinicionEvento(
        codigo="shipment.requirement_rejected",
        critico=True,
        asunto="Un documento fue rechazado",
        mensaje=f"Se rechazó un documento de una de sus cargas y hay que volver a cargarlo. {_ENTRAR}",
        con_referencia=f"Se rechazó un documento de la carga {{ref}} y hay que volver a cargarlo. {_ENTRAR}",
        ruta="/shipments/{id}",
    ),
    "shipment.permit_review": DefinicionEvento(
        codigo="shipment.permit_review",
        critico=True,
        asunto="Su carga podría requerir un permiso especial",
        mensaje=f"Una de sus cargas quedó marcada para revisión de permisos o inspección. {_ENTRAR}",
        con_referencia=f"La carga {{ref}} quedó marcada para revisión de permisos o inspección. {_ENTRAR}",
        ruta="/shipments/{id}",
    ),
    "dispatch.status_changed": DefinicionEvento(
        codigo="dispatch.status_changed",
        critico=True,
        asunto="Actualización de su solicitud de despacho",
        mensaje=f"Una de sus solicitudes de despacho cambió de estado. {_ENTRAR}",
        con_referencia=f"La solicitud de despacho {{ref}} cambió de estado. {_ENTRAR}",
        ruta="/despachos/{id}",
    ),
    "shipment.dispatched": DefinicionEvento(
        codigo="shipment.dispatched",
        critico=True,
        asunto="Su carga fue despachada",
        mensaje=f"Una de sus cargas salió despachada. {_ENTRAR}",
        con_referencia=f"La carga {{ref}} salió despachada. {_ENTRAR}",
        ruta="/shipments/{id}",
    ),
    "shipment.delivered": DefinicionEvento(
        codigo="shipment.delivered",
        critico=True,
        asunto="Su carga fue entregada",
        mensaje=f"Se registró la entrega de una de sus cargas. {_ENTRAR}",
        con_referencia=f"Se registró la entrega de la carga {{ref}}. {_ENTRAR}",
        ruta="/shipments/{id}",
    ),
    "shipment.corrected": DefinicionEvento(
        codigo="shipment.corrected",
        critico=True,
        asunto="Corrección en el estado de una carga",
        mensaje=f"Se corrigió el estado de una de sus cargas. {_ENTRAR}",
        con_referencia=f"Se corrigió el estado de la carga {{ref}}. {_ENTRAR}",
        ruta="/shipments/{id}",
    ),
    "account.security_alert": DefinicionEvento(
        codigo="account.security_alert",
        critico=True,
        asunto="Alerta de seguridad en su cuenta",
        mensaje=(
            "Se detectó actividad relevante para la seguridad de su cuenta. "
            "Si no la reconoce, cambie su contraseña. Entre al sistema para revisarla."
        ),
        ruta="/sesiones",
    ),
    # --- Los seis del sistema anterior ---
    #
    # Cada uno reemplaza una plantilla de `templates/emails/` del sistema Django,
    # con dos diferencias respecto del original: no lleva shipper, carrier,
    # método ni la lista completa de cargas, y no lleva adjuntos. El
    # identificador y el enlace alcanzan para saber de qué se habla.
    "shipment.stored": DefinicionEvento(
        # Reemplaza `new_warehouse.html`. En el sistema viejo salía al crear un
        # Warehouse Receipt; acá sale cuando la carga queda almacenada, que es
        # el mismo hecho para el cliente: su mercadería está en bodega y contada.
        codigo="shipment.stored",
        critico=True,
        asunto="Su carga está en bodega",
        mensaje=f"Recibimos una carga suya y ya está almacenada. {_ENTRAR}",
        con_referencia=f"Recibimos su carga {{ref}} y ya está almacenada. {_ENTRAR}",
        ruta="/shipments/{id}",
    ),
    "dispatch.requested": DefinicionEvento(
        # Reemplaza `dispatch_received.html`: el acuse al cliente.
        codigo="dispatch.requested",
        critico=True,
        asunto="Recibimos su solicitud de despacho",
        mensaje=f"Recibimos su solicitud de despacho y la estamos revisando. {_ENTRAR}",
        con_referencia=(
            f"Recibimos su solicitud de despacho {{ref}} y la estamos revisando. {_ENTRAR}"
        ),
        ruta="/despachos/{id}",
    ),
    "dispatch.requested_internal": DefinicionEvento(
        # Reemplaza `dispatch_request.html`: el aviso a Operaciones. Es el único
        # de los seis que no va a un cliente.
        codigo="dispatch.requested_internal",
        critico=True,
        asunto="Nueva solicitud de despacho para revisar",
        mensaje=f"Un cliente creó una solicitud de despacho y está pendiente de aprobación. {_ENTRAR}",
        con_referencia=(
            f"La solicitud de despacho {{ref}} está pendiente de aprobación. {_ENTRAR}"
        ),
        ruta="/despachos/{id}",
    ),
    "dispatch.approved": DefinicionEvento(
        # Reemplaza `dispatch_approved.html`.
        codigo="dispatch.approved",
        critico=True,
        asunto="Su solicitud de despacho fue aprobada",
        mensaje=f"Aprobamos su solicitud de despacho y empezamos a prepararla. {_ENTRAR}",
        con_referencia=(
            f"Aprobamos su solicitud de despacho {{ref}} y empezamos a prepararla. {_ENTRAR}"
        ),
        ruta="/despachos/{id}",
    ),
    "dispatch.bol_available": DefinicionEvento(
        # Reemplaza `dispatch_bol.html`. El original adjuntaba los PDF del Bill
        # of Lading; acá el correo avisa y los documentos se descargan desde el
        # sistema, con enlaces firmados que vencen (ADR-0008).
        codigo="dispatch.bol_available",
        critico=True,
        asunto="Bill of Lading disponible",
        mensaje=(
            "Se completó un despacho suyo y el Bill of Lading ya está disponible "
            f"para descargar. {_ENTRAR}"
        ),
        con_referencia=(
            "Se completó el despacho {ref} y el Bill of Lading ya está disponible "
            f"para descargar. {_ENTRAR}"
        ),
        ruta="/despachos/{id}",
    ),
    "account.invitation": DefinicionEvento(
        # Reemplaza `credentials.html`, que mandaba usuario y contraseña en texto
        # plano. Acá viaja un enlace de un solo uso que vence a las 48 horas y
        # donde la persona elige su propia contraseña: nadie más la conoce nunca.
        # El enlace no se arma con `ruta`, sino en el servicio de invitaciones,
        # porque lleva el token.
        codigo="account.invitation",
        critico=True,
        asunto="Su acceso a AMVARMAR",
        mensaje=(
            "Le crearon una cuenta en el sistema de AMVARMAR. Use el enlace para "
            "elegir su contraseña. Vence en 48 horas y sirve una sola vez."
        ),
        ruta="/invitacion/{id}",
    ),
    "account.password_reset": DefinicionEvento(
        # No viene del sistema anterior: es el correo que `POST /auth/password/forgot`
        # prometía en un comentario y nunca mandaba. Sin esto el endpoint emite
        # el token, lo guarda y nadie lo recibe.
        codigo="account.password_reset",
        critico=True,
        asunto="Restablecer su contraseña",
        mensaje=(
            "Pidió restablecer la contraseña de su cuenta de AMVARMAR. Use el "
            "enlace para elegir una nueva; vence en una hora y sirve una sola vez. "
            "Si no fue usted, ignore este mensaje: su contraseña no cambió."
        ),
        ruta="/restablecer-contrasena?token={id}",
    ),
    # --- No críticos ---
    "shipment.status_changed": DefinicionEvento(
        codigo="shipment.status_changed",
        critico=False,
        asunto="Su carga avanzó de estado",
        mensaje=f"Una de sus cargas cambió de estado. {_ENTRAR}",
        con_referencia=f"La carga {{ref}} cambió de estado. {_ENTRAR}",
        ruta="/shipments/{id}",
    ),
    "document.archiving_soon": DefinicionEvento(
        codigo="document.archiving_soon",
        critico=False,
        asunto="Documentos próximos a archivarse",
        mensaje=(
            "Documentos de una de sus cargas cumplen seis meses y pasarán al historial "
            f"archivado. No se elimina nada. {_ENTRAR}"
        ),
        con_referencia=(
            "Documentos de la carga {ref} cumplen seis meses y pasarán al historial "
            f"archivado. No se elimina nada. {_ENTRAR}"
        ),
        ruta="/shipments/{id}",
    ),
}


def definicion(codigo: str) -> DefinicionEvento | None:
    return EVENTOS.get(codigo)


def texto(evento: DefinicionEvento, referencia: str | None) -> str:
    """El cuerpo del aviso, con el identificador si lo hay.

    Sin identificador se usa la frase genérica en vez de dejar el hueco: un
    correo que diga "la carga  salió despachada" se lee como un error del
    sistema, y el caso ocurre de verdad con las cargas migradas que llegaron sin
    WR ni factura.
    """
    if referencia and evento.con_referencia:
        return evento.con_referencia.format(ref=referencia)
    return evento.mensaje

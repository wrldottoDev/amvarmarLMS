"""Composición y envío de correo (Paso 4.2).

Dos responsabilidades separadas: `componer()` arma el mensaje y no toca la red;
`enviar()` habla con el relay. Así el contenido se puede probar sin levantar un
servidor SMTP, y el envío se puede probar contra Mailpit sin volver a armar el
mensaje.

ADR-0008 y su enmienda del 25-08-2026: el correo lleva el texto del catálogo,
el identificador de la carga o del despacho, y un enlace al sistema. Nunca lleva
adjuntos, credenciales, ni el detalle comercial (shipper, carrier, pesos).
"""

from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path

import aiosmtplib
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from app.core.config import get_settings
from app.modules.notifications.catalog import DefinicionEvento
from app.modules.notifications.catalog import texto as texto_del_evento

# Versión de las plantillas. Se guarda en cada entrega para poder saber con qué
# texto se envió un correo viejo, incluso después de rediseñar la plantilla.
PLANTILLA_VERSION = "2"

_DIRECTORIO = Path(__file__).parent / "templates"

# `autoescape` en el HTML: el mensaje sale del catálogo, pero el enlace lleva un
# identificador y una plantilla sin escapar es una inyección esperando su turno.
_entorno = Environment(
    loader=FileSystemLoader(_DIRECTORIO),
    autoescape=select_autoescape(["html"]),
    # StrictUndefined: una variable que falta rompe al renderizar, en vez de
    # mandar un correo con un hueco en blanco.
    undefined=StrictUndefined,
)


class EnvioFallido(Exception):
    """El relay rechazó el mensaje o no respondió."""


@dataclass(frozen=True)
class CorreoCompuesto:
    asunto: str
    texto: str
    html: str
    enlace: str


def construir_enlace(evento: DefinicionEvento, resource_id: str | None) -> str:
    """La URL del aviso. Sin recurso, la pantalla general en vez de un enlace roto.

    El corte va en la primera llave y no en `"/{"`: hay rutas donde el hueco
    está en la query (`?token={id}`), y buscar la barra dejaría el `{id}`
    literal dentro del enlace. Después se descarta la query entera, porque lo
    que quedaría es un parámetro a medias (`?token=`).
    """
    base = get_settings().frontend_base_url.rstrip("/")

    if resource_id:
        return f"{base}{evento.ruta.format(id=resource_id)}"

    ruta = evento.ruta.split("{")[0].partition("?")[0].rstrip("/")
    return f"{base}{ruta}"


def componer(
    evento: DefinicionEvento,
    *,
    resource_id: str | None,
    referencia: str | None = None,
    enlace: str | None = None,
) -> CorreoCompuesto:
    """Arma el correo. No toca la red.

    `referencia` es el identificador que el cliente reconoce —WR, número de
    factura, número de solicitud—. `enlace` solo se pasa cuando no se puede
    derivar de la ruta, que hoy es únicamente la invitación: su URL lleva un
    token de un solo uso, no el id del recurso.

    El asunto también lleva el identificador: en una bandeja con veinte correos
    de AMVARMAR, veinte asuntos idénticos no dicen cuál es cuál.
    """
    enlace = enlace or construir_enlace(evento, resource_id)
    asunto = f"{evento.asunto} — {referencia}" if referencia else evento.asunto
    contexto = {
        "asunto": asunto,
        "mensaje": texto_del_evento(evento, referencia),
        "enlace": enlace,
    }

    return CorreoCompuesto(
        asunto=asunto,
        texto=_entorno.get_template("base.txt").render(**contexto),
        html=_entorno.get_template("base.html").render(**contexto),
        enlace=enlace,
    )


def _armar_mensaje(destino: str, correo: CorreoCompuesto) -> EmailMessage:
    settings = get_settings()
    mensaje = EmailMessage()
    mensaje["From"] = settings.email_from
    mensaje["To"] = destino
    mensaje["Subject"] = correo.asunto
    # Cabecera estándar para que los clientes no generen respuestas automáticas
    # ni avisos de vacaciones contra una casilla que nadie lee.
    mensaje["Auto-Submitted"] = "auto-generated"
    mensaje.set_content(correo.texto)
    mensaje.add_alternative(correo.html, subtype="html")
    return mensaje


async def enviar(destino: str, correo: CorreoCompuesto) -> None:
    """Entrega al relay. Lanza `EnvioFallido` para que el outbox reintente."""
    settings = get_settings()

    try:
        await aiosmtplib.send(
            _armar_mensaje(destino, correo),
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password,
            start_tls=settings.smtp_use_tls,
            timeout=settings.smtp_timeout_seconds,
        )
    except Exception as error:
        # Se envuelve para que el manejador del outbox no tenga que conocer los
        # tipos de excepción de aiosmtplib.
        raise EnvioFallido(str(error)) from error

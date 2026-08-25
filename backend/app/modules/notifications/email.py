"""Composición y envío de correo (Paso 4.2).

Dos responsabilidades separadas: `componer()` arma el mensaje y no toca la red;
`enviar()` habla con el relay. Así el contenido se puede probar sin levantar un
servidor SMTP, y el envío se puede probar contra Mailpit sin volver a armar el
mensaje.

ADR-0008: el correo no lleva adjuntos ni datos de negocio. Solo el texto fijo
del catálogo y un enlace al sistema.
"""

from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path

import aiosmtplib
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from app.core.config import get_settings
from app.modules.notifications.catalog import DefinicionEvento

# Versión de las plantillas. Se guarda en cada entrega para poder saber con qué
# texto se envió un correo viejo, incluso después de rediseñar la plantilla.
PLANTILLA_VERSION = "1"

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
    base = get_settings().frontend_base_url.rstrip("/")
    ruta = evento.ruta.format(id=resource_id) if resource_id else evento.ruta.split("/{")[0]
    return f"{base}{ruta}"


def componer(evento: DefinicionEvento, *, resource_id: str | None) -> CorreoCompuesto:
    enlace = construir_enlace(evento, resource_id)
    contexto = {"asunto": evento.asunto, "mensaje": evento.mensaje, "enlace": enlace}

    return CorreoCompuesto(
        asunto=evento.asunto,
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

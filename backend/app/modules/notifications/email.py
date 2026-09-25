"""Composición y envío de correo (Paso 4.2).

Dos responsabilidades separadas: `componer()` arma el mensaje y no toca la red;
`enviar()` habla con el relay. Así el contenido se puede probar sin levantar un
servidor SMTP, y el envío se puede probar contra Mailpit sin volver a armar el
mensaje.

ADR-0008 y sus enmiendas: el correo lleva el texto del catálogo, el
identificador de la carga o del despacho, y un enlace al sistema. Nunca lleva
credenciales ni el detalle comercial (shipper, carrier, pesos). Desde el
2026-09-25 el aviso del Bill of Lading adjunta el PDF, como el sistema anterior,
hasta `TOPE_ADJUNTOS_BYTES`; ningún otro aviso lleva adjuntos.
"""

from collections.abc import Sequence
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
PLANTILLA_VERSION = "3"

# Tope del total adjunto a un correo. Gmail y Outlook rechazan por encima de
# ~20-25 MB, y el base64 del adjunto suma un tercio: 10 MB de archivo son ~14
# MB de mensaje, con margen. Si el archivo pesa más, el correo lo dice y se
# descarga desde el sistema.
TOPE_ADJUNTOS_BYTES = 10 * 1024 * 1024

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
class Adjunto:
    nombre: str
    media_type: str
    contenido: bytes


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
    nota: str | None = None,
    con_adjuntos: bool = False,
) -> CorreoCompuesto:
    """Arma el correo. No toca la red.

    `nota` es un párrafo extra (hoy, qué se adjuntó o por qué no), y
    `con_adjuntos` cambia el pie, que si no promete que no hay adjuntos.

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
        "nota": nota,
        "con_adjuntos": con_adjuntos,
    }

    return CorreoCompuesto(
        asunto=asunto,
        texto=_entorno.get_template("base.txt").render(**contexto),
        html=_entorno.get_template("base.html").render(**contexto),
        enlace=enlace,
    )


def _armar_mensaje(
    destino: str, correo: CorreoCompuesto, adjuntos: Sequence[Adjunto] = ()
) -> EmailMessage:
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
    for adjunto in adjuntos:
        principal, _, secundario = adjunto.media_type.partition("/")
        mensaje.add_attachment(
            adjunto.contenido,
            maintype=principal or "application",
            subtype=secundario or "octet-stream",
            filename=adjunto.nombre,
        )
    return mensaje


async def enviar(destino: str, correo: CorreoCompuesto, adjuntos: Sequence[Adjunto] = ()) -> None:
    """Entrega al relay. Lanza `EnvioFallido` para que el outbox reintente."""
    settings = get_settings()

    try:
        await aiosmtplib.send(
            _armar_mensaje(destino, correo, adjuntos),
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            # `aiosmtplib` decide autenticar con `is not None`, no con
            # verdadero/falso: un string vacío igual dispara el login y
            # Mailpit (sin TLS) lo rechaza porque no anuncia AUTH. `or None`
            # asegura que "sin usuario" viaje como ausencia real, no vacía.
            username=settings.smtp_username or None,
            password=settings.smtp_password or None,
            start_tls=settings.smtp_use_tls,
            use_tls=settings.smtp_use_ssl,
            timeout=settings.smtp_timeout_seconds,
        )
    except Exception as error:
        # Se envuelve para que el manejador del outbox no tenga que conocer los
        # tipos de excepción de aiosmtplib.
        raise EnvioFallido(str(error)) from error

"""Contratos HTTP del asistente (ADR-0012)."""

import base64
import binascii
from typing import Any

from pydantic import BaseModel, Field, field_validator


class MensajeChat(BaseModel):
    rol: str = Field(pattern="^(user|assistant)$")
    contenido: str = Field(max_length=4000)


class ContextoPagina(BaseModel):
    """Opcional: qué pantalla mira el usuario, para que AMVI entienda "esta
    carga" sin que el modelo tenga que inferirlo del texto libre, y para
    orientarlo desde donde está en vez de describirle la aplicación entera."""

    ruta: str | None = Field(default=None, max_length=200)
    recurso_id: str | None = Field(default=None, max_length=64)


# Lo que el proveedor sabe leer (`provider.describir_factura`).
MEDIA_TYPES_ADJUNTO = ("application/pdf", "image/jpeg", "image/png", "image/webp")

# Base64 infla ~33%, así que esto son ~7.5 MB de archivo real. El tope de
# subida al expediente es mucho mayor (250 MB): ahí el archivo se guarda, acá
# viaja entero dentro de una petición y de ahí al proveedor en la misma vuelta.
LIMITE_ADJUNTO_BASE64 = 10 * 1024 * 1024


class AdjuntoChat(BaseModel):
    """Un archivo que la persona suelta en el chat para que AMVI lo lea.

    NO se guarda en ningún lado (ADR-0017): vive lo que dura el turno, se lee
    una vez y se descarta. Lo que queda de él es la propuesta que el modelo
    arma con los datos extraídos, que la persona revisa antes de confirmar.
    Para archivar la factura está el expediente de la carga, que valida bytes,
    tipo real y hash — cosas que este camino no hace porque no guarda nada.
    """

    nombre: str = Field(max_length=200)
    media_type: str = Field(pattern=r"^(application/pdf|image/(jpeg|png|webp))$")
    contenido_base64: str = Field(max_length=LIMITE_ADJUNTO_BASE64)

    @field_validator("contenido_base64")
    @classmethod
    def validar_base64(cls, valor: str) -> str:
        try:
            base64.b64decode(valor, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValueError("El contenido del adjunto no es un base64 válido.") from error
        return valor


class RespondRequest(BaseModel):
    # El historial de la conversación hasta acá. `store=false` en el proveedor
    # significa que no hay un id de respuesta anterior que reenviar: el
    # frontend reenvía la conversación en cada turno (ADR-0012, enmienda
    # 2026-09). El backend guarda cada turno para el historial del usuario
    # (enmienda 2026-10-08), pero no lo usa para armar el siguiente.
    mensajes: list[MensajeChat] = Field(min_length=1, max_length=20)
    contexto_pagina: ContextoPagina | None = None
    # Lo genera el frontend una vez por conversación (`crypto.randomUUID()`)
    # y lo reenvía en cada turno. Namespacea el tope de tokens en Redis y es
    # la clave de la conversación guardada de ESE usuario (`client_key`): otro
    # usuario con el mismo valor tiene otra conversación, nunca la misma.
    conversacion_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    # Una factura, un packing list, la foto de un papel. Se lee en este turno
    # y se descarta; ver `AdjuntoChat`.
    adjunto: AdjuntoChat | None = None


class CapabilitiesResponse(BaseModel):
    disponible: bool
    nombre: str
    # Explícito y no derivado de `herramientas`: la interfaz no tiene por qué
    # saber que adjuntar depende del mismo permiso que proponer escrituras.
    puede_adjuntar: bool
    # None = sin tope (personal interno). Un número = lo que le queda al
    # cliente este mes.
    cuota_restante: int | None
    herramientas: list[str]


class ConfirmarPropuestaRequest(BaseModel):
    # Lo que la persona corrigió antes de confirmar. Puede diferir de lo que
    # el modelo propuso — eso es exactamente el punto de la revisión humana.
    campos: dict[str, Any] = Field(default_factory=dict)


class PropuestaConfirmadaResponse(BaseModel):
    id: str
    action_code: str
    resultado: dict[str, Any]

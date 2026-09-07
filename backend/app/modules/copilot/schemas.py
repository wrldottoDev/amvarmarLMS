"""Contratos HTTP del asistente (ADR-0012)."""

from typing import Any

from pydantic import BaseModel, Field


class MensajeChat(BaseModel):
    rol: str = Field(pattern="^(user|assistant)$")
    contenido: str = Field(max_length=4000)


class ContextoPagina(BaseModel):
    """Opcional: qué pantalla mira el usuario, para que AMVI entienda "esta
    carga" sin que el modelo tenga que inferirlo del texto libre."""

    ruta: str | None = Field(default=None, max_length=200)
    recurso_id: str | None = Field(default=None, max_length=64)


class RespondRequest(BaseModel):
    # El historial completo de la conversación hasta acá. `store=false` (el
    # chat no se persiste ni en el proveedor ni en el backend) significa que
    # no hay un id de respuesta anterior que reenviar: el frontend mantiene la
    # conversación de la pestaña y la reenvía entera en cada turno
    # (ADR-0012, enmienda 2026-09).
    mensajes: list[MensajeChat] = Field(min_length=1, max_length=20)
    contexto_pagina: ContextoPagina | None = None
    # Opaco: solo namespacea el contador de tokens en Redis (ADR-0012,
    # enmienda 2026-09-06). El frontend lo genera una vez por conversación
    # (`crypto.randomUUID()`) y lo reenvía en cada turno; nunca es una
    # referencia a datos guardados, así que no hace falta validarlo contra
    # nada — solo acotar su forma para no dejar crecer claves de Redis sin
    # límite.
    conversacion_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class CapabilitiesResponse(BaseModel):
    disponible: bool
    nombre: str
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

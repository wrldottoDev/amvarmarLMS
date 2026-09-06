"""Registro cerrado de acciones confirmables (ADR-0012, enmienda 2026-09).

Reemplaza a `endpoint_confirmacion`. Antes, la propuesta le decía al frontend
qué `{method, path}` llamar para confirmar — eso permite que un cliente arme
una llamada a cualquier endpoint. Ahora la propuesta lleva un `action_code`
cerrado, y el backend resuelve qué ejecutar. El frontend nunca aporta ruta ni
método.

En esta fase el registro queda declarado pero vacío: los ejecutores de
confirmación se agregan a partir de la Fase 4, cuando exista la primera
herramienta de escritura real con su flujo de confirmación completo.
"""

from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.rbac.service import PermisosEfectivos


class AccionCopilot(StrEnum):
    """Un valor por cada herramienta de clase ESCRITURA del catálogo.

    El valor coincide con el nombre de la herramienta: son la misma acción
    vista desde dos lados (la propone el modelo, la confirma la persona), y
    mantenerlos iguales evita una tabla de traducción que se puede desincronizar.
    """

    PROCESAR_FACTURA_OCR = "procesar_factura_ocr"
    CREAR_PREALERTA_BORRADOR = "crear_prealerta_borrador"


# Firma que todo ejecutor de confirmación implementa. Recibe la propuesta ya
# validada (actor dueño, PENDING, sin vencer, permisos revalidados) y los
# campos que la persona confirmó — que pueden diferir de los que el modelo
# propuso. Devuelve el `result` que se guarda en la propuesta.
EjecutorConfirmacion = Callable[
    [AsyncSession, PermisosEfectivos, UUID, dict[str, Any]], Awaitable[dict[str, Any]]
]

# Vacío a propósito: sin ejecutores registrados, ninguna acción puede
# confirmarse todavía. `service.py` debe rechazar la confirmación con un error
# explícito si el `action_code` no tiene ejecutor, nunca ejecutar en silencio.
REGISTRO_DE_CONFIRMACION: dict[AccionCopilot, EjecutorConfirmacion] = {}

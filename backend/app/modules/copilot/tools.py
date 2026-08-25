"""Catálogo de herramientas del asistente (ADR-0012).

Cada herramienta declara el permiso que exige. Ese permiso se usa dos veces:

1. Para **filtrar** qué herramientas se le ofrecen al modelo — así no alucina
   capacidades que el usuario no tiene.
2. Para **verificar** al ejecutar.

El segundo chequeo no es redundante. Un modelo puede emitir una llamada a una
herramienta que no se le ofreció; si el ejecutor confiara en el filtro previo,
esa llamada pasaría. La regla del proyecto — el backend valida siempre — vale
igual para un cliente humano que para un modelo.

Ninguna herramienta recibe `company_id`: el alcance sale del JWT del actor. Si
el modelo pudiera elegir la empresa, bastaría convencerlo con texto para leer
datos ajenos.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.modules.rbac.catalog import Perm
from app.modules.rbac.service import PermisosEfectivos


class ClaseHerramienta(StrEnum):
    """Lectura se ejecuta directo; escritura SIEMPRE pasa por confirmación."""

    LECTURA = "LECTURA"
    ESCRITURA = "ESCRITURA"


# --- Argumentos de entrada ---


class ConsultarEstadoCargaArgs(BaseModel):
    shipment_number: str = Field(
        max_length=32,
        description="Código de la carga (SHP-2026-000123) o número de factura.",
    )


class CotizarEnvioArgs(BaseModel):
    peso_kg: float = Field(gt=0, le=50000)
    valor_declarado_usd: float = Field(ge=0, le=10_000_000)


class ProcesarFacturaOcrArgs(BaseModel):
    # Referencia a un documento YA subido por el flujo normal (Paso 3.1), no el
    # archivo en sí: el asistente no es una vía alterna para subir contenido
    # que se saltearía la validación de MIME y el antivirus.
    document_id: str = Field(description="ID de un documento ya cargado y verificado.")


class CrearPrealertaBorradorArgs(BaseModel):
    descripcion: str = Field(max_length=2000)
    origen_location_code: str | None = Field(default=None, max_length=16)
    destino_location_code: str | None = Field(default=None, max_length=16)
    factura: str | None = Field(default=None, max_length=180)
    peso_kg: float | None = Field(default=None, ge=0)


# --- Contrato de confirmación humana ---


class CampoPropuesto(BaseModel):
    """Un campo extraído o inferido, con qué tan seguro está el modelo.

    La confianza es POR CAMPO, no global: la interfaz resalta lo dudoso en vez
    de presentar todo como igual de confiable.
    """

    nombre: str
    etiqueta: str
    # `None` es un resultado válido, no un error: un campo que no aparece se
    # devuelve vacío con una advertencia. Inventarlo sería peor.
    valor: str | float | Decimal | None
    confianza: float = Field(ge=0, le=1)
    editable: bool = True


class EndpointConfirmacion(BaseModel):
    """Qué llamada hace el frontend al confirmar.

    Informativo: el backend igual valida permiso y payload cuando la
    confirmación llega. La propuesta no autoriza nada por sí sola.
    """

    method: str
    path: str
    requiere_idempotency_key: bool = True


class PropuestaAccion(BaseModel):
    """Lo que devuelve una herramienta de escritura.

    NO impacta la base. La persona revisa, corrige y confirma; la confirmación
    es una llamada normal a la API, con su permiso y su auditoría.
    """

    id: str
    accion: str
    titulo: str
    requiere_confirmacion: bool = True
    # Una propuesta vieja no se confirma: los datos pudieron cambiar.
    expira_en: str
    campos: list[CampoPropuesto]
    advertencias: list[str] = Field(default_factory=list)
    endpoint_confirmacion: EndpointConfirmacion


# --- Catálogo ---


@dataclass(frozen=True)
class DefinicionHerramienta:
    nombre: str
    descripcion: str
    argumentos: type[BaseModel]
    permiso: str
    clase: ClaseHerramienta


HERRAMIENTAS: dict[str, DefinicionHerramienta] = {
    "consultar_estado_carga": DefinicionHerramienta(
        nombre="consultar_estado_carga",
        descripcion=(
            "Consulta el estado, la ruta, la ETA y los pendientes de una carga. "
            "Acepta el código de carga o un número de factura. Devuelve el estado "
            "logístico y los requisitos pendientes como datos separados."
        ),
        argumentos=ConsultarEstadoCargaArgs,
        permiso=Perm.SHIPMENTS_READ,
        clase=ClaseHerramienta.LECTURA,
    ),
    "cotizar_envio": DefinicionHerramienta(
        nombre="cotizar_envio",
        descripcion=(
            "Calcula una cotización estimada según peso y valor declarado. "
            "Es una estimación, no un precio en firme."
        ),
        argumentos=CotizarEnvioArgs,
        permiso=Perm.SHIPMENTS_READ,
        clase=ClaseHerramienta.LECTURA,
    ),
    "procesar_factura_ocr": DefinicionHerramienta(
        nombre="procesar_factura_ocr",
        descripcion=(
            "Extrae número de guía, proveedor, monto y cliente de una factura ya "
            "cargada. Devuelve una propuesta para revisión humana; no registra nada."
        ),
        argumentos=ProcesarFacturaOcrArgs,
        permiso=Perm.DOCUMENTS_UPLOAD_INTERNAL,
        clase=ClaseHerramienta.ESCRITURA,
    ),
    "crear_prealerta_borrador": DefinicionHerramienta(
        nombre="crear_prealerta_borrador",
        descripcion=(
            "Prepara el borrador de una prealerta a partir de la conversación. "
            "Devuelve una propuesta para revisión humana; no crea la carga."
        ),
        argumentos=CrearPrealertaBorradorArgs,
        permiso=Perm.SHIPMENTS_CREATE,
        clase=ClaseHerramienta.ESCRITURA,
    ),
}


def herramientas_disponibles(permisos: PermisosEfectivos) -> list[DefinicionHerramienta]:
    """Qué herramientas ofrecerle al modelo en este turno.

    Filtrar acá evita que el modelo prometa lo que no puede hacer. NO sustituye
    la verificación al ejecutar: ver `puede_ejecutar`.
    """
    otorgados = permisos.codigos()
    return [h for h in HERRAMIENTAS.values() if h.permiso in otorgados]


def puede_ejecutar(nombre: str, permisos: PermisosEfectivos) -> bool:
    """Verificación al ejecutar. Este es el chequeo que realmente protege.

    Un modelo puede emitir una llamada a una herramienta que no se le ofreció;
    sin este chequeo, esa llamada se ejecutaría.
    """
    definicion = HERRAMIENTAS.get(nombre)
    if definicion is None:
        return False
    return definicion.permiso in permisos.codigos()


def esquema_para_proveedor(definicion: DefinicionHerramienta) -> dict[str, Any]:
    """Traduce la definición al formato de tool calling del proveedor.

    Se genera desde el modelo Pydantic para que el esquema que ve la IA y el que
    valida la entrada no puedan divergir.
    """
    return {
        "type": "function",
        "function": {
            "name": definicion.nombre,
            "description": definicion.descripcion,
            "parameters": definicion.argumentos.model_json_schema(),
            "strict": True,
        },
    }


def esquemas_para_proveedor(
    definiciones: Sequence[DefinicionHerramienta],
) -> list[dict[str, Any]]:
    return [esquema_para_proveedor(d) for d in definiciones]

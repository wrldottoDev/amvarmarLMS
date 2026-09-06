"""Catálogo de herramientas del asistente (ADR-0012).

Cada herramienta declara una regla de autorización (`ReglaAutz`). Esa regla se
usa dos veces:

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

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field

from app.modules.copilot.acciones import AccionCopilot
from app.modules.rbac.catalog import Perm
from app.modules.rbac.service import PermisosEfectivos


class ClaseHerramienta(StrEnum):
    """Lectura se ejecuta directo; escritura SIEMPRE pasa por confirmación."""

    LECTURA = "LECTURA"
    ESCRITURA = "ESCRITURA"


# --- Regla de autorización (ADR-0012, enmienda 2026-09) ---
#
# Un solo `permiso: str` no alcanza. Tres casos reales lo demuestran:
#   - Solo alcance, sin permiso de dominio: no existe `dispatch_requests.read`,
#     el listado de despachos autoriza únicamente por scope del JWT.
#   - Permiso alternativo: cambiar el estado de una carga hacia adelante exige
#     un permiso distinto que hacia atrás.
#   - Permisos combinados: crear una carga desde un documento exige subir
#     Y crear, ambos a la vez.


class ReglaAutz(ABC):
    """Evalúa si un actor puede usar una herramienta. No decide el alcance de
    los datos que esa herramienta ve — eso lo sigue resolviendo el ejecutor
    con el JWT, como el resto de la API."""

    @abstractmethod
    def permite(self, permisos: PermisosEfectivos) -> bool: ...

    @abstractmethod
    def permisos_referenciados(self) -> frozenset[str]:
        """Permisos de dominio que esta regla exige, para poder auditar que el
        catálogo nunca inventa un permiso propio del copiloto (ver
        `test_el_asistente_no_amplia_lo_que_el_usuario_ya_podia_hacia`)."""


@dataclass(frozen=True)
class SoloAlcance(ReglaAutz):
    """Sin permiso de dominio que exigir: el filtro real es el alcance del
    JWT, que aplica el ejecutor en la consulta misma."""

    def permite(self, permisos: PermisosEfectivos) -> bool:
        return True

    def permisos_referenciados(self) -> frozenset[str]:
        return frozenset()


@dataclass(frozen=True)
class Requiere(ReglaAutz):
    permiso: str

    def permite(self, permisos: PermisosEfectivos) -> bool:
        return self.permiso in permisos.codigos()

    def permisos_referenciados(self) -> frozenset[str]:
        return frozenset({self.permiso})


@dataclass(frozen=True)
class RequiereAlguno(ReglaAutz):
    """Alguno de los permisos alcanza. Ej.: transicionar hacia adelante O
    hacia atrás, según hacia dónde proponga ir el modelo — la dirección
    concreta la valida el ejecutor, esto solo filtra si el actor tiene AL
    MENOS uno de los dos."""

    permisos: tuple[str, ...]

    def permite(self, permisos: PermisosEfectivos) -> bool:
        otorgados = permisos.codigos()
        return any(p in otorgados for p in self.permisos)

    def permisos_referenciados(self) -> frozenset[str]:
        return frozenset(self.permisos)


@dataclass(frozen=True)
class RequiereTodos(ReglaAutz):
    """Los dos permisos a la vez. Ej.: crear una carga desde un documento
    exige poder subir documentos internos Y crear cargas."""

    permisos: tuple[str, ...]

    def permite(self, permisos: PermisosEfectivos) -> bool:
        otorgados = permisos.codigos()
        return all(p in otorgados for p in self.permisos)

    def permisos_referenciados(self) -> frozenset[str]:
        return frozenset(self.permisos)


# --- Argumentos de entrada ---


class ConsultarEstadoCargaArgs(BaseModel):
    shipment_number: str = Field(
        max_length=32,
        description="Código de la carga (SHP-2026-000123) o número de factura.",
    )


class BuscarCargasArgs(BaseModel):
    q: str = Field(
        max_length=120,
        description=(
            "Texto libre: número SHP, WR, shipper, carrier, factura, PO, "
            "tracking o contenedor. Busca por coincidencia parcial."
        ),
    )


class ExplicarQueFaltaArgs(BaseModel):
    shipment_number: str = Field(
        max_length=32, description="Código de la carga (SHP-2026-000123) o número de factura."
    )


class MisPendientesArgs(BaseModel):
    """Sin argumentos: siempre es sobre el actor de la conversación, nunca
    sobre otra persona — evita que el modelo intente pasar un `user_id`."""


class ObtenerPreferenciasArgs(BaseModel):
    """Sin argumentos: las columnas visibles son las del actor, no las de
    otro usuario que el modelo pudiera nombrar."""


class ConsultarDespachoArgs(BaseModel):
    dispatch_number: str = Field(
        max_length=32, description="Código del despacho (DSP-2026-000123)."
    )


class ListarDespachosArgs(BaseModel):
    estado: str | None = Field(
        default=None,
        max_length=20,
        description="Filtra por estado: PENDING, APPROVED, PREPARING, DISPATCHED o COMPLETED.",
    )


class ComoHagoArgs(BaseModel):
    tema: str = Field(max_length=120, description="Qué quiere hacer o entender en la plataforma.")


class ProcesarFacturaOcrArgs(BaseModel):
    # Referencia a un documento YA subido por el flujo normal (Paso 3.1), no el
    # archivo en sí: el asistente no es una vía alterna para subir contenido
    # que se saltearía la validación de MIME.
    document_id: str = Field(description="ID de un documento ya cargado y verificado.")


class CrearPrealertaBorradorArgs(BaseModel):
    descripcion: str = Field(max_length=2000)
    origen_location_code: str | None = Field(default=None, max_length=16)
    destino_location_code: str | None = Field(default=None, max_length=16)
    factura: str | None = Field(default=None, max_length=180)
    peso_kg: float | None = Field(default=None, ge=0)
    # Toda carga necesita al menos una pieza desde su creación (invariante de
    # dominio, `shipments/gestion.py::_validar_bultos`). Si la conversación no
    # trae este dato, el borrador queda incompleto y lo pide al confirmar —
    # nunca se inventa un tipo o cantidad de bulto.
    bulto_tipo: str | None = Field(
        default=None,
        max_length=10,
        description="Tipo de bulto si lo mencionaron: PALLET, BOX, DRUM, BUNDLE u OTHER.",
    )
    bulto_cantidad: int | None = Field(default=None, ge=1)


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


class PropuestaAccion(BaseModel):
    """Lo que devuelve una herramienta de escritura.

    NO impacta la base. La persona revisa, corrige y confirma contra
    `POST /copilot/proposals/{id}/confirm`.

    `action_code` reemplaza a lo que antes era `endpoint_confirmacion`
    (ADR-0012, enmienda 2026-09): el cliente nunca aporta ruta ni método, solo
    un valor de un registro cerrado que el backend resuelve.
    """

    id: str
    action_code: AccionCopilot
    titulo: str
    resumen_efecto: str
    requiere_confirmacion: bool = True
    # Una propuesta vieja no se confirma: los datos pudieron cambiar.
    expira_en: str
    campos: list[CampoPropuesto]
    advertencias: list[str] = Field(default_factory=list)


# --- Catálogo ---


@dataclass(frozen=True)
class DefinicionHerramienta:
    nombre: str
    descripcion: str
    argumentos: type[BaseModel]
    autorizacion: ReglaAutz
    clase: ClaseHerramienta
    # Solo las de clase ESCRITURA lo tienen: es la clave que resuelve qué
    # ejecutor de confirmación corre cuando la persona aprueba la propuesta.
    action_code: AccionCopilot | None = field(default=None)

    def __post_init__(self) -> None:
        if self.clase is ClaseHerramienta.ESCRITURA and self.action_code is None:
            raise ValueError(f"{self.nombre}: toda herramienta ESCRITURA necesita action_code.")
        if self.clase is ClaseHerramienta.LECTURA and self.action_code is not None:
            raise ValueError(f"{self.nombre}: una herramienta LECTURA no lleva action_code.")


HERRAMIENTAS: dict[str, DefinicionHerramienta] = {
    "consultar_estado_carga": DefinicionHerramienta(
        nombre="consultar_estado_carga",
        descripcion=(
            "Consulta el estado, la ruta, la ETA y los pendientes de una carga. "
            "Acepta el código de carga o un número de factura. Devuelve el estado "
            "logístico y los requisitos pendientes como datos separados."
        ),
        argumentos=ConsultarEstadoCargaArgs,
        autorizacion=Requiere(Perm.SHIPMENTS_READ),
        clase=ClaseHerramienta.LECTURA,
    ),
    "buscar_cargas": DefinicionHerramienta(
        nombre="buscar_cargas",
        descripcion=(
            "Busca cargas por texto libre (SHP, WR, shipper, carrier, factura, PO, "
            "tracking o contenedor). Devuelve como mucho 10 resultados con estado y ETA."
        ),
        argumentos=BuscarCargasArgs,
        autorizacion=Requiere(Perm.SHIPMENTS_READ),
        clase=ClaseHerramienta.LECTURA,
    ),
    "explicar_que_falta": DefinicionHerramienta(
        nombre="explicar_que_falta",
        descripcion=(
            "Traduce a lenguaje llano qué documentos o requisitos siguen abiertos "
            "para una carga, y quién debe resolverlos."
        ),
        argumentos=ExplicarQueFaltaArgs,
        autorizacion=Requiere(Perm.SHIPMENTS_READ),
        clase=ClaseHerramienta.LECTURA,
    ),
    "mis_pendientes": DefinicionHerramienta(
        nombre="mis_pendientes",
        descripcion=(
            "Todo lo que le toca al actor de esta conversación: cargas con "
            "documentos pendientes o listas para despachar, priorizadas."
        ),
        argumentos=MisPendientesArgs,
        autorizacion=Requiere(Perm.SHIPMENTS_READ),
        clase=ClaseHerramienta.LECTURA,
    ),
    "obtener_preferencias": DefinicionHerramienta(
        nombre="obtener_preferencias",
        descripcion="Qué columnas tiene visibles el actor en el listado de cargas.",
        argumentos=ObtenerPreferenciasArgs,
        autorizacion=Requiere(Perm.SHIPMENTS_READ),
        clase=ClaseHerramienta.LECTURA,
    ),
    "consultar_despacho": DefinicionHerramienta(
        nombre="consultar_despacho",
        descripcion="Detalle de una solicitud de despacho por su código (DSP-2026-000123).",
        argumentos=ConsultarDespachoArgs,
        autorizacion=Requiere(Perm.SHIPMENTS_READ),
        clase=ClaseHerramienta.LECTURA,
    ),
    "listar_despachos": DefinicionHerramienta(
        nombre="listar_despachos",
        descripcion="Lista las solicitudes de despacho del actor, opcionalmente filtradas por estado.",
        argumentos=ListarDespachosArgs,
        autorizacion=Requiere(Perm.SHIPMENTS_READ),
        clase=ClaseHerramienta.LECTURA,
    ),
    "como_hago": DefinicionHerramienta(
        nombre="como_hago",
        descripcion=(
            "Guía sobre cómo usar la plataforma misma (no sobre datos de negocio). "
            "Si no hay una guía escrita para el tema, lo dice en vez de inventar."
        ),
        argumentos=ComoHagoArgs,
        autorizacion=Requiere(Perm.COPILOT_USE),
        clase=ClaseHerramienta.LECTURA,
    ),
    "procesar_factura_ocr": DefinicionHerramienta(
        nombre="procesar_factura_ocr",
        descripcion=(
            "Extrae número de guía, proveedor, monto y cliente de una factura ya "
            "cargada. Devuelve una propuesta para revisión humana; no registra nada."
        ),
        argumentos=ProcesarFacturaOcrArgs,
        autorizacion=Requiere(Perm.DOCUMENTS_UPLOAD_INTERNAL),
        clase=ClaseHerramienta.ESCRITURA,
        action_code=AccionCopilot.PROCESAR_FACTURA_OCR,
    ),
    "crear_prealerta_borrador": DefinicionHerramienta(
        nombre="crear_prealerta_borrador",
        descripcion=(
            "Prepara el borrador de una prealerta a partir de la conversación. "
            "Devuelve una propuesta para revisión humana; no crea la carga."
        ),
        argumentos=CrearPrealertaBorradorArgs,
        # Combinado, no solo el de dominio (ADR-0012, enmienda 2026-09):
        # `COPILOT_TOOLS_DRAFT` es el permiso "puede pedirle propuestas al
        # asistente"; `SHIPMENTS_CREATE` es el de dominio que igual exige
        # `gestion.crear` al confirmar. Los dos existen en el catálogo desde
        # la Fase 2 pero ninguna herramienta los combinaba todavía.
        autorizacion=RequiereTodos((Perm.COPILOT_TOOLS_DRAFT, Perm.SHIPMENTS_CREATE)),
        clase=ClaseHerramienta.ESCRITURA,
        action_code=AccionCopilot.CREAR_PREALERTA_BORRADOR,
    ),
}


def herramientas_disponibles(permisos: PermisosEfectivos) -> list[DefinicionHerramienta]:
    """Qué herramientas ofrecerle al modelo en este turno.

    Filtrar acá evita que el modelo prometa lo que no puede hacer. NO sustituye
    la verificación al ejecutar: ver `puede_ejecutar`.
    """
    return [h for h in HERRAMIENTAS.values() if h.autorizacion.permite(permisos)]


def puede_ejecutar(nombre: str, permisos: PermisosEfectivos) -> bool:
    """Verificación al ejecutar. Este es el chequeo que realmente protege.

    Un modelo puede emitir una llamada a una herramienta que no se le ofreció;
    sin este chequeo, esa llamada se ejecutaría.
    """
    definicion = HERRAMIENTAS.get(nombre)
    if definicion is None:
        return False
    return definicion.autorizacion.permite(permisos)


def herramientas_de_clase(clase: ClaseHerramienta) -> Sequence[DefinicionHerramienta]:
    return [h for h in HERRAMIENTAS.values() if h.clase is clase]

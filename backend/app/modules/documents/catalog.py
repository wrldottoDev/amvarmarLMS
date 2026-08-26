"""Catálogo de tipos de documento — fuente única de verdad (ADR-0003 + ADR-0009)."""

from dataclasses import dataclass

from app.modules.documents.models import DocumentTypeCode, ProvidedBy
from app.modules.shipments.models import ShipmentStatus

# Formatos base que acepta casi todo tipo. DOCX se convierte a PDF en el
# procesamiento (ADR-0009); se acepta como entrada, no como formato final.
_PDF_E_IMAGENES = ["PDF", "JPG", "JPEG", "PNG", "WEBP", "HEIC"]
_CON_DOCX = [*_PDF_E_IMAGENES, "DOCX"]


@dataclass(frozen=True)
class DefinicionTipoDocumento:
    label: str
    description: str
    provided_by: ProvidedBy
    allowed_formats: list[str]
    # En qué estado se exige. None = no bloquea ninguna transición.
    required_before_status: str | None


TIPOS_DOCUMENTO: dict[str, DefinicionTipoDocumento] = {
    DocumentTypeCode.COMMERCIAL_INVOICE: DefinicionTipoDocumento(
        label="Factura comercial",
        description=(
            "Obligatoria para la carga. Si el proveedor la entrega directo a AMVARMAR, "
            "Operaciones la carga y deja de ser una acción pendiente del cliente."
        ),
        provided_by=ProvidedBy.CLIENT,
        allowed_formats=_CON_DOCX,
        required_before_status=ShipmentStatus.DISPATCHED,
    ),
    DocumentTypeCode.SLI: DefinicionTipoDocumento(
        label="SLI",
        description=(
            "Obligatoria solo para cargas originadas en una bodega que la exige "
            "(aplicabilidad automática, ligada a ADR-0005)."
        ),
        provided_by=ProvidedBy.CLIENT,
        allowed_formats=_CON_DOCX,
        required_before_status=ShipmentStatus.DISPATCHED,
    ),
    DocumentTypeCode.PACKING_LIST: DefinicionTipoDocumento(
        label="Packing list",
        description=(
            "Lo emite el proveedor y lo carga Operaciones. Visible para el cliente, "
            "pero nunca aparece como pendiente suyo."
        ),
        provided_by=ProvidedBy.STAFF,
        # Único tipo que acepta hoja de cálculo (ADR-0009, confirmado).
        allowed_formats=[*_CON_DOCX, "XLSX", "CSV"],
        required_before_status=ShipmentStatus.DISPATCHED,
    ),
    DocumentTypeCode.BL: DefinicionTipoDocumento(
        label="Bill of Lading",
        description="Se genera y carga después del despacho, para consulta del cliente.",
        provided_by=ProvidedBy.STAFF,
        allowed_formats=_PDF_E_IMAGENES,
        # No bloquea nada: existe después de DISPATCHED (ADR-0006).
        required_before_status=None,
    ),
    DocumentTypeCode.SPECIAL_PERMIT: DefinicionTipoDocumento(
        label="Permiso especial",
        description=(
            "Obligatorio solo si Operaciones determina que la mercancía requiere "
            "inspección o autorización. El cliente no puede descartar la advertencia."
        ),
        provided_by=ProvidedBy.CLIENT,
        allowed_formats=_PDF_E_IMAGENES,
        required_before_status=ShipmentStatus.DISPATCHED,
    ),
    DocumentTypeCode.WAREHOUSE_RECEIPT: DefinicionTipoDocumento(
        label="Warehouse Receipt",
        description=(
            "El comprobante que emite la bodega al recibir la mercancía. Lo carga "
            "Operaciones; el cliente lo consulta."
        ),
        provided_by=ProvidedBy.STAFF,
        # Sin ZIP a propósito. El sistema anterior guardaba 190 de estos como
        # comprimidos y esos se traen igual —el migrador escribe en `documents`
        # directo, sin pasar por la validación de formato—, pero de acá en
        # adelante un WR nuevo se sube en PDF: un ZIP puede traer cualquier cosa
        # adentro y el antivirus no ve dentro de un comprimido cifrado.
        allowed_formats=_CON_DOCX,
        # No bloquea ninguna transición. Que la carga tenga WR se exige por
        # `shipment_references`, no por este documento: son cosas distintas, el
        # número y el papel.
        required_before_status=None,
    ),
    DocumentTypeCode.PROOF_OF_DELIVERY: DefinicionTipoDocumento(
        label="Prueba de entrega",
        description=(
            "Documento firmado, fotografía, comprobante del transportista o "
            "confirmación electrónica. Obligatoria para registrar la entrega."
        ),
        provided_by=ProvidedBy.STAFF,
        allowed_formats=_PDF_E_IMAGENES,
        required_before_status=ShipmentStatus.DELIVERED,
    ),
}

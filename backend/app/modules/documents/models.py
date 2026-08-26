"""Catálogo de tipos de documento (ADR-0003).

Se crea aquí, en Fase 2, porque `shipment_requirements` necesita apuntar a él.
Las tablas `documents` y `shipment_documents` llegan en Fase 3.
"""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, CITEXT, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin, UUIDPk


class DocumentTypeCode(StrEnum):
    COMMERCIAL_INVOICE = "COMMERCIAL_INVOICE"
    SLI = "SLI"
    PACKING_LIST = "PACKING_LIST"
    BL = "BL"
    SPECIAL_PERMIT = "SPECIAL_PERMIT"
    PROOF_OF_DELIVERY = "PROOF_OF_DELIVERY"
    WAREHOUSE_RECEIPT = "WAREHOUSE_RECEIPT"


class ProvidedBy(StrEnum):
    """Quién debe aportar el documento.

    Distinto de "obligatorio para la carga": el packing list es obligatorio pero
    lo carga Operaciones, así que el cliente no debe verlo como pendiente suyo
    (ADR-0003).
    """

    CLIENT = "CLIENT"
    STAFF = "STAFF"


class DocumentType(Base, TimestampMixin):
    __tablename__ = "document_types"

    id: Mapped[UUIDPk]
    code: Mapped[str] = mapped_column(CITEXT, unique=True)
    label: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)

    provided_by: Mapped[str] = mapped_column(String(16))

    # Formatos aceptados POR TIPO, no una lista global (ADR-0003 + ADR-0009):
    # solo el packing list admite hoja de cálculo.
    allowed_formats: Mapped[list[str]] = mapped_column(ARRAY(String(16)))

    # Si es obligatorio, en qué estado se exige. NULL = no bloquea ninguna
    # transición (el BL se carga después del despacho).
    required_before_status: Mapped[str | None] = mapped_column(String(32))

    is_active: Mapped[bool] = mapped_column(server_default=text("true"))

    __table_args__ = (
        CheckConstraint("provided_by IN ('CLIENT', 'STAFF')", name="provided_by_valido"),
        CheckConstraint("cardinality(allowed_formats) > 0", name="al_menos_un_formato"),
    )


class UploadStatus(StrEnum):
    """Estado técnico del pipeline de subida (ADR-0009).

    Distinto del estado del requisito que el documento satisface (negocio, en
    `shipment_requirements`): un archivo puede estar `READY` y aun así tener su
    requisito en `REJECTED` porque Operaciones no lo aceptó.
    """

    UPLOADING = "UPLOADING"
    PROCESSING = "PROCESSING"
    READY = "READY"
    FAILED = "FAILED"


class Document(Base):
    """Un archivo en storage privado.

    `storage_key` NUNCA es una URL pública. El acceso siempre pasa por una URL
    firmada de corta duración que el backend emite tras verificar permisos.
    """

    __tablename__ = "documents"

    id: Mapped[UUIDPk]
    company_id: Mapped[UUID] = mapped_column(ForeignKey("companies.id", ondelete="RESTRICT"))
    uploaded_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))

    storage_provider: Mapped[str] = mapped_column(String(20))
    storage_key: Mapped[str] = mapped_column(String(500), unique=True)

    # El nombre que puso el usuario y el que se usa al servir. Se separan porque
    # un nombre de archivo puede traer rutas (`../../etc/passwd`), caracteres de
    # control o secuencias que rompan la cabecera Content-Disposition.
    original_name: Mapped[str] = mapped_column(String(255))
    safe_name: Mapped[str] = mapped_column(String(255))

    # MIME detectado de los BYTES, no el que declaró el cliente.
    media_type: Mapped[str] = mapped_column(String(150))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))

    upload_status: Mapped[str] = mapped_column(String(20))

    # ADR-0007: los archivos NO se eliminan. A los 6 meses se recomprimen y se
    # archivan; estas columnas dejan trazabilidad de cuánto se redujo.
    archived_compressed_at: Mapped[datetime | None]
    original_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    # Un PDF firmado no se recomprime (invalidaría la firma): solo se comprime
    # el contenedor, de forma transparente al servir.
    is_digitally_signed: Mapped[bool] = mapped_column(server_default=text("false"))

    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    # Invalidación lógica (ADR-0004): el archivo sigue existiendo, deja de
    # aparecer en el uso normal.
    deleted_at: Mapped[datetime | None]

    __table_args__ = (
        CheckConstraint(
            "upload_status IN ('UPLOADING', 'PROCESSING', 'READY', 'FAILED')",
            name="upload_status_valido",
        ),
        CheckConstraint("size_bytes > 0", name="archivo_no_vacio"),
        CheckConstraint("length(sha256) = 64", name="sha256_completo"),
        Index("ix_documents_empresa_creacion", "company_id", text("created_at DESC")),
        # Para detectar duplicados dentro de una empresa sin recorrer todo.
        Index("ix_documents_empresa_hash", "company_id", "sha256"),
    )


class ShipmentDocument(Base):
    """Vincula un documento con una carga y su tipo.

    Un mismo documento puede amparar varias cargas (una factura que cubre tres
    envíos), por eso es tabla aparte y no un FK en `documents`.
    """

    __tablename__ = "shipment_documents"

    shipment_id: Mapped[UUID] = mapped_column(
        ForeignKey("shipments.id", ondelete="CASCADE"), primary_key=True
    )
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="RESTRICT"), primary_key=True
    )
    document_type_id: Mapped[UUID] = mapped_column(
        ForeignKey("document_types.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))

    __table_args__ = (Index("ix_shipment_documents_document", "document_id"),)


class SystemSetting(Base):
    """Ajustes editables sin desplegar (ADR-0009).

    Los límites de archivo viven acá porque `SUPER_ADMIN` debe poder cambiarlos.
    `is_secret` marca que el valor es una REFERENCIA a un gestor de secretos,
    no el secreto mismo: esta tabla no guarda credenciales.
    """

    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(CITEXT, primary_key=True)
    value: Mapped[dict[str, object]] = mapped_column(JSONB)
    is_secret: Mapped[bool] = mapped_column(server_default=text("false"))
    updated_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(server_default=text("now()"))

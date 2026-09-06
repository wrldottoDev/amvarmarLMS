from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    CHAR,
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin, UUIDPk


class ShipmentStatus(StrEnum):
    """Catálogo de ADR-0001. Los valores viven en base; este enum es para que
    el código no use strings sueltos."""

    PRE_ALERT = "PRE_ALERT"
    IN_TRANSIT = "IN_TRANSIT"
    RECEIVED = "RECEIVED"
    STORED = "STORED"
    DISPATCH_REQUESTED = "DISPATCH_REQUESTED"
    PREPARING = "PREPARING"
    DISPATCHED = "DISPATCHED"
    DELIVERED = "DELIVERED"
    CANCELLED = "CANCELLED"


class StatusCategory(StrEnum):
    PRE_ARRIVAL = "PRE_ARRIVAL"
    WAREHOUSE = "WAREHOUSE"
    DISPATCH = "DISPATCH"
    FINAL = "FINAL"


class ShipmentStatusRow(Base):
    """Catálogo de estados, sembrado por script idempotente.

    En base y no hardcodeado: agregar un estado o desactivar uno no debe exigir
    un despliegue. `is_active` permite retirar uno sin borrar las cargas que ya
    lo tienen — un FK roto sería peor que un estado obsoleto.
    """

    __tablename__ = "shipment_statuses"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    label: Mapped[str] = mapped_column(String(80))
    category: Mapped[str] = mapped_column(String(20))
    sort_order: Mapped[int] = mapped_column(SmallInteger, unique=True)
    is_terminal: Mapped[bool] = mapped_column(server_default=text("false"))
    is_active: Mapped[bool] = mapped_column(server_default=text("true"))

    __table_args__ = (
        CheckConstraint(
            "category IN ('PRE_ARRIVAL', 'WAREHOUSE', 'DISPATCH', 'FINAL')",
            name="category_valida",
        ),
    )


class ShipmentStatusTransition(Base):
    """Qué transiciones existen y qué permiso exige cada una.

    El permiso viaja en la fila, no en el código: por eso `OPS_AGENT` puede
    cancelar desde `PRE_ALERT` pero no desde `IN_TRANSIT` (ADR-0004) sin que el
    motor de transiciones tenga que consultar nombres de rol.
    """

    __tablename__ = "shipment_status_transitions"

    from_status_code: Mapped[str] = mapped_column(
        ForeignKey("shipment_statuses.code", ondelete="RESTRICT"), primary_key=True
    )
    to_status_code: Mapped[str] = mapped_column(
        ForeignKey("shipment_statuses.code", ondelete="RESTRICT"), primary_key=True
    )
    required_permission_id: Mapped[UUID] = mapped_column(
        ForeignKey("permissions.id", ondelete="RESTRICT")
    )

    # Marca las transiciones que exigen justificación (retrocesos, cancelación,
    # reapertura, reversión de entrega) — ADR-0001.
    requires_reason: Mapped[bool] = mapped_column(server_default=text("false"))

    is_active: Mapped[bool] = mapped_column(server_default=text("true"))

    __table_args__ = (
        CheckConstraint("from_status_code <> to_status_code", name="estados_distintos"),
    )


class TransportMode(StrEnum):
    SEA = "SEA"
    AIR = "AIR"
    LAND = "LAND"


class Location(Base, TimestampMixin):
    """Catálogo de ubicaciones (ADR-0005).

    El origen de una carga NO se determina por texto libre ni por el shipper:
    apunta a una fila de aquí.
    """

    __tablename__ = "locations"

    id: Mapped[UUIDPk]
    country_code: Mapped[str] = mapped_column(CHAR(2))
    city_code: Mapped[str] = mapped_column(String(10))
    # País + ciudad, ej. `US-MIA`. Evita ambigüedad entre ciudades homónimas.
    location_code: Mapped[str] = mapped_column(String(16), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    is_active: Mapped[bool] = mapped_column(server_default=text("true"))

    __table_args__ = (
        CheckConstraint("country_code = upper(country_code)", name="country_code_mayusculas"),
        UniqueConstraint("country_code", "city_code", name="uq_locations_pais_ciudad"),
    )


class Facility(Base, TimestampMixin):
    """Instalación de AMVARMAR en una ubicación (ADR-0005).

    `uses_warehouse_receipt` es lo que habilita el WR. La regla NO compara
    ciudad: abrir una bodega nueva es activar este flag, no tocar código.
    """

    __tablename__ = "facilities"

    id: Mapped[UUIDPk]
    location_id: Mapped[UUID] = mapped_column(ForeignKey("locations.id", ondelete="RESTRICT"))
    facility_code: Mapped[str] = mapped_column(String(24), unique=True)
    facility_type: Mapped[str] = mapped_column(String(24))
    uses_warehouse_receipt: Mapped[bool] = mapped_column(server_default=text("false"))
    is_active: Mapped[bool] = mapped_column(server_default=text("true"))

    __table_args__ = (
        CheckConstraint(
            "facility_type IN ('WAREHOUSE', 'OFFICE', 'PARTNER')", name="facility_type_valido"
        ),
    )


class Shipment(Base, TimestampMixin):
    """El expediente de carga: la entidad central del sistema.

    Reemplaza al `Warehouse` del sistema legacy. Diferencia clave: la identidad
    es un UUID y el `shipment_number` es un código de negocio, no la PK. En el
    legacy el `wr_number` era la PK, lo que amarraba toda la identidad a un dato
    operativo de una sola bodega.
    """

    __tablename__ = "shipments"

    id: Mapped[UUIDPk]

    # Código legible, formato SHP-YYYY-NNNNNN. Único, pero NUNCA la PK.
    # El default lo calcula la base (`siguiente_shipment_number()`, definida en
    # la migración): así el migrador legacy de Fase 5, que inserta por SQL
    # directo, toma números del mismo pozo y no colisiona.
    shipment_number: Mapped[str] = mapped_column(
        String(32), unique=True, server_default=text("siguiente_shipment_number()")
    )

    company_id: Mapped[UUID] = mapped_column(ForeignKey("companies.id", ondelete="RESTRICT"))
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))

    # Alcance ASSIGNED de ADR-0004: qué OPS_AGENT atiende esta carga.
    assigned_to: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    current_status_code: Mapped[str] = mapped_column(
        ForeignKey("shipment_statuses.code", ondelete="RESTRICT")
    )

    transport_mode: Mapped[str | None] = mapped_column(String(20))

    # ADR-0005: origen estructurado, no texto libre.
    origin_location_id: Mapped[UUID] = mapped_column(
        ForeignKey("locations.id", ondelete="RESTRICT")
    )
    origin_facility_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("facilities.id", ondelete="RESTRICT")
    )
    destination_location_id: Mapped[UUID] = mapped_column(
        ForeignKey("locations.id", ondelete="RESTRICT")
    )
    # Dirección de entrega concreta: la ubicación da ciudad y país, esto la calle.
    destination_address: Mapped[str | None] = mapped_column(String(240))

    current_location: Mapped[str | None] = mapped_column(String(180))
    description: Mapped[str | None] = mapped_column(Text)

    estimated_arrival_at: Mapped[datetime | None]
    actual_arrival_at: Mapped[datetime | None]
    received_at: Mapped[datetime | None]
    stored_at: Mapped[datetime | None]
    dispatched_at: Mapped[datetime | None]
    delivered_at: Mapped[datetime | None]

    # Datos comerciales que el sistema viejo manejaba como campos propios y
    # mostraba como columnas del listado. Se conservan así —y no dentro de la
    # descripción— porque con ellos se busca, se ordena y se cobra.
    shipper: Mapped[str | None] = mapped_column(String(180))
    carrier: Mapped[str | None] = mapped_column(String(180))
    # Pies cúbicos (CFTS en el sistema viejo). Se usa para cubicaje.
    foots_cft: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))

    # El legacy conserva ambos valores tal como se migraron y deja la fuente en
    # NULL. En una carga nueva o al editar su peso, el backend recibe una sola
    # fuente y recalcula ambas representaciones.
    weight_lb: Mapped[Decimal | None] = mapped_column(Numeric(14, 3))
    weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(14, 3))
    weight_source_unit: Mapped[str | None] = mapped_column(String(2))
    volumetric_weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(14, 3))
    volume_m3: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    package_count: Mapped[int] = mapped_column(server_default=text("0"))

    # Bloqueo optimista: todo PATCH lo exige y el UPDATE lo incrementa en la
    # misma sentencia. Dos ediciones simultáneas: una pasa, la otra da 409.
    row_version: Mapped[int] = mapped_column(server_default=text("1"))

    # ADR-0003: el cuestionario de prealerta puede marcar posible mercancía
    # regulada. Es una advertencia; Operaciones determina si el permiso aplica.
    permit_review_required: Mapped[bool] = mapped_column(server_default=text("false"))

    # ADR-0002: rastro de la migración legacy. `legacy_status` guarda el valor
    # crudo del sistema viejo y no se borra nunca.
    legacy_review_required: Mapped[bool] = mapped_column(server_default=text("false"))
    legacy_status: Mapped[str | None] = mapped_column(String(20))

    # Ocultar una carga (equivale al "eliminar" del sistema viejo). No borra:
    # sus documentos, su línea de tiempo y su auditoría siguen existiendo, y se
    # puede recuperar. Quién la ocultó y por qué quedan registrados.
    hidden_at: Mapped[datetime | None]
    hidden_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    hidden_reason: Mapped[str | None] = mapped_column(Text)

    # ADR-0007: retención. El job de archivado de Fase 4 consulta estos campos;
    # se crean ya para no necesitar una migración retroactiva.
    retention_until: Mapped[datetime | None]
    archived_at: Mapped[datetime | None]
    legal_hold: Mapped[bool] = mapped_column(server_default=text("false"))
    legal_hold_reason: Mapped[str | None] = mapped_column(Text)

    deleted_at: Mapped[datetime | None]

    __table_args__ = (
        CheckConstraint(
            "transport_mode IS NULL OR transport_mode IN ('SEA', 'AIR', 'LAND')",
            name="transport_mode_valido",
        ),
        CheckConstraint("weight_kg IS NULL OR weight_kg >= 0", name="peso_no_negativo"),
        CheckConstraint(
            "weight_source_unit IS NULL OR (weight_source_unit IN ('KG', 'LB') "
            "AND weight_kg > 0 AND weight_lb > 0)",
            name="peso_fuente_valido",
        ),
        CheckConstraint(
            "volumetric_weight_kg IS NULL OR volumetric_weight_kg >= 0",
            name="peso_volumetrico_no_negativo",
        ),
        CheckConstraint("volume_m3 IS NULL OR volume_m3 >= 0", name="volumen_no_negativo"),
        CheckConstraint("package_count >= 0", name="bultos_no_negativos"),
        CheckConstraint("row_version > 0", name="row_version_positiva"),
        # ADR-0007: un legal hold sin motivo no se puede auditar después.
        CheckConstraint(
            "legal_hold = false OR legal_hold_reason IS NOT NULL",
            name="legal_hold_exige_motivo",
        ),
        # Índices parciales: excluyen borradas, archivadas y ocultas. Son la
        # mayoría del volumen con el tiempo y nunca aparecen en consultas
        # operativas; el predicado tiene que coincidir con el del WHERE o
        # PostgreSQL no puede usar el índice.
        Index(
            "ix_shipments_empresa_estado",
            "company_id",
            "current_status_code",
            text("updated_at DESC"),
            postgresql_where=text(
                "deleted_at IS NULL AND archived_at IS NULL AND hidden_at IS NULL"
            ),
        ),
        Index(
            "ix_shipments_empresa_eta",
            "company_id",
            "estimated_arrival_at",
            postgresql_where=text(
                "estimated_arrival_at IS NOT NULL AND deleted_at IS NULL "
                "AND archived_at IS NULL AND hidden_at IS NULL"
            ),
        ),
        Index(
            "ix_shipments_asignado",
            "assigned_to",
            "current_status_code",
            postgresql_where=text(
                "deleted_at IS NULL AND archived_at IS NULL AND hidden_at IS NULL"
            ),
        ),
        Index(
            "ix_shipments_empresa_creacion",
            "company_id",
            text("created_at DESC"),
            postgresql_where=text(
                "deleted_at IS NULL AND archived_at IS NULL AND hidden_at IS NULL"
            ),
        ),
        # Para el job de archivado de Fase 4.
        Index(
            "ix_shipments_retencion",
            "retention_until",
            postgresql_where=text("archived_at IS NULL AND retention_until IS NOT NULL"),
        ),
        # Búsqueda combinada por similitud (q=...): gin_trgm_ops soporta ILIKE
        # parcial sin escanear toda la tabla.
        Index(
            "ix_shipments_numero_trgm",
            "shipment_number",
            postgresql_using="gin",
            postgresql_ops={"shipment_number": "gin_trgm_ops"},
        ),
        Index(
            "ix_shipments_shipper_trgm",
            "shipper",
            postgresql_using="gin",
            postgresql_ops={"shipper": "gin_trgm_ops"},
        ),
        Index(
            "ix_shipments_carrier_trgm",
            "carrier",
            postgresql_using="gin",
            postgresql_ops={"carrier": "gin_trgm_ops"},
        ),
    )


class ReferenceType(StrEnum):
    """Cómo se identifica comercialmente una carga.

    `INVOICE` es la referencia general; `WR` es opcional y solo aplica a cargas
    con bodega que lo use (ADR-0005).
    """

    INVOICE = "INVOICE"
    WR = "WR"
    PO = "PO"
    TRACKING = "TRACKING"
    CONTAINER = "CONTAINER"
    BL = "BL"
    OTHER = "OTHER"


class PackageType(StrEnum):
    PALLET = "PALLET"
    BOX = "BOX"
    DRUM = "DRUM"
    BUNDLE = "BUNDLE"
    OTHER = "OTHER"


class EventType(StrEnum):
    CREATED = "CREATED"
    STATUS_CHANGED = "STATUS_CHANGED"
    LOCATION_UPDATED = "LOCATION_UPDATED"
    REFERENCE_ADDED = "REFERENCE_ADDED"
    DOCUMENT_ADDED = "DOCUMENT_ADDED"
    REQUIREMENT_OPENED = "REQUIREMENT_OPENED"
    REQUIREMENT_FULFILLED = "REQUIREMENT_FULFILLED"
    DELIVERY_DISPUTED = "DELIVERY_DISPUTED"
    DELIVERY_DISPUTE_RESOLVED = "DELIVERY_DISPUTE_RESOLVED"
    NOTE = "NOTE"
    # Una corrección NO edita el evento equivocado: agrega uno nuevo que lo
    # explica. La tabla es append-only.
    CORRECTION = "CORRECTION"


class ShipmentReference(Base):
    """Identificadores comerciales de la carga.

    La regla de Miami (solo hay WR si la bodega de origen lo usa) NO vive aquí:
    depende de `facilities.uses_warehouse_receipt`, que es una tabla distinta, y
    un CHECK no puede consultarla. Se aplica en `policies.py`.
    """

    __tablename__ = "shipment_references"

    id: Mapped[UUIDPk]
    shipment_id: Mapped[UUID] = mapped_column(ForeignKey("shipments.id", ondelete="CASCADE"))

    reference_type: Mapped[str] = mapped_column(String(24))
    value: Mapped[str] = mapped_column(String(180))
    # WR se compara normalizado dentro de la facility que lo emitió. Un trigger
    # deriva ambos campos también para inserciones del migrador o SQL directo.
    facility_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("facilities.id", ondelete="RESTRICT")
    )
    normalized_value: Mapped[str] = mapped_column(String(180))
    issuer: Mapped[str | None] = mapped_column(String(180))
    is_primary: Mapped[bool] = mapped_column(server_default=text("false"))

    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))

    __table_args__ = (
        CheckConstraint(
            "reference_type IN ('INVOICE', 'WR', 'PO', 'TRACKING', 'CONTAINER', 'BL', 'OTHER')",
            name="reference_type_valido",
        ),
        CheckConstraint("length(btrim(value)) > 0", name="valor_no_vacio"),
        UniqueConstraint(
            "shipment_id", "reference_type", "value", name="uq_shipment_references_valor"
        ),
        CheckConstraint(
            "reference_type <> 'WR' OR facility_id IS NOT NULL",
            name="wr_exige_facility",
        ),
        # Búsqueda por número de factura o tracking: es como el cliente encuentra
        # su carga cuando no recuerda el shipment_number.
        Index("ix_shipment_references_busqueda", "reference_type", "value"),
        Index(
            "uq_shipment_references_wr_facility",
            "facility_id",
            "normalized_value",
            unique=True,
            postgresql_where=text("reference_type = 'WR'"),
        ),
        Index(
            "ix_shipment_references_value_trgm",
            "value",
            postgresql_using="gin",
            postgresql_ops={"value": "gin_trgm_ops"},
        ),
    )


class ShipmentPackage(Base):
    """Bultos de la carga. Reemplaza a `PieceWarehouse` del sistema legacy."""

    __tablename__ = "shipment_packages"

    id: Mapped[UUIDPk]
    shipment_id: Mapped[UUID] = mapped_column(ForeignKey("shipments.id", ondelete="CASCADE"))

    package_type: Mapped[str] = mapped_column(String(24))
    quantity: Mapped[int]
    description: Mapped[str | None] = mapped_column(String(300))

    weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(14, 3))
    length_cm: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    width_cm: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    height_cm: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))

    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))

    __table_args__ = (
        CheckConstraint(
            "package_type IN ('PALLET', 'BOX', 'DRUM', 'BUNDLE', 'OTHER')",
            name="package_type_valido",
        ),
        CheckConstraint("quantity > 0", name="cantidad_positiva"),
        CheckConstraint("weight_kg IS NULL OR weight_kg >= 0", name="peso_no_negativo"),
        CheckConstraint("length_cm IS NULL OR length_cm >= 0", name="largo_no_negativo"),
        CheckConstraint("width_cm IS NULL OR width_cm >= 0", name="ancho_no_negativo"),
        CheckConstraint("height_cm IS NULL OR height_cm >= 0", name="alto_no_negativo"),
        Index("ix_shipment_packages_shipment", "shipment_id"),
    )


class ShipmentEvent(Base):
    """Línea de tiempo append-only.

    Un trigger de base impide UPDATE y DELETE: la inmutabilidad no depende de
    que el código de aplicación se porte bien, ni de que nadie abra una consola
    de SQL. Corregir un error se hace agregando un evento `CORRECTION`.

    `ON DELETE RESTRICT` sobre el shipment (no CASCADE): borrar una carga no
    puede borrar su historia. Las cargas se marcan con `deleted_at`, no se
    eliminan.
    """

    __tablename__ = "shipment_events"

    id: Mapped[UUIDPk]
    shipment_id: Mapped[UUID] = mapped_column(ForeignKey("shipments.id", ondelete="RESTRICT"))

    event_type: Mapped[str] = mapped_column(String(40))
    from_status_code: Mapped[str | None] = mapped_column(
        ForeignKey("shipment_statuses.code", ondelete="RESTRICT")
    )
    to_status_code: Mapped[str | None] = mapped_column(
        ForeignKey("shipment_statuses.code", ondelete="RESTRICT")
    )

    title: Mapped[str] = mapped_column(String(160))
    description: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(String(180))

    # Dos tiempos distintos: `occurred_at` es cuándo pasó el hecho,
    # `recorded_at` cuándo se registró. Permite cargar un movimiento atrasado
    # sin falsear la auditoría.
    occurred_at: Mapped[datetime]
    recorded_at: Mapped[datetime] = mapped_column(server_default=text("now()"))

    actor_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    # `metadata` está reservado en SQLAlchemy declarativo (`Base.metadata`), así
    # que el atributo se llama distinto pero la columna conserva el nombre del
    # documento de arquitectura.
    datos: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSONB, server_default=text("'{}'::jsonb")
    )

    __table_args__ = (
        # Desempate por `id`: sin él, dos eventos con el mismo `occurred_at`
        # saldrían en orden arbitrario y distinto en cada consulta.
        Index("ix_shipment_events_timeline", "shipment_id", text("occurred_at DESC"), "id"),
    )


class RequirementType(StrEnum):
    DOCUMENT = "DOCUMENT"
    INFORMATION = "INFORMATION"
    PAYMENT = "PAYMENT"
    ACTION = "ACTION"


class RequirementStatus(StrEnum):
    """Dos juegos de estados según el tipo (ADR-0003).

    Los documentales tienen ciclo propio porque subir un archivo no equivale a
    que Operaciones lo haya aceptado: `UPLOADED` no satisface el requisito,
    `VERIFIED` sí.
    """

    # Genéricos (INFORMATION, PAYMENT, ACTION)
    OPEN = "OPEN"
    FULFILLED = "FULFILLED"

    # Documentales
    PENDING = "PENDING"
    UPLOADED = "UPLOADED"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    # "Nunca aplicó a esta carga" — NO es lo mismo que WAIVED, que significa
    # "aplicaba y Operaciones lo exoneró". Mezclarlos perdería en auditoría la
    # diferencia entre saltarse algo obligatorio y algo que nunca lo fue.
    NOT_APPLICABLE = "NOT_APPLICABLE"

    # Comunes a ambos juegos
    WAIVED = "WAIVED"
    CANCELLED = "CANCELLED"


# Estados que dejan de bloquear una transición.
ESTADOS_RESUELTOS: frozenset[str] = frozenset(
    {
        RequirementStatus.FULFILLED,
        RequirementStatus.VERIFIED,
        RequirementStatus.NOT_APPLICABLE,
        RequirementStatus.WAIVED,
        RequirementStatus.CANCELLED,
    }
)


class ShipmentRequirement(Base):
    """Lo que falta para que la carga avance.

    Concepto central del proyecto: "faltan documentos" NO es un estado de la
    carga. Una carga puede estar `IN_TRANSIT` y tener a la vez un requisito
    abierto de factura. El estado logístico y lo pendiente son dos ejes
    distintos, y la interfaz los muestra por separado.
    """

    __tablename__ = "shipment_requirements"

    id: Mapped[UUIDPk]
    shipment_id: Mapped[UUID] = mapped_column(ForeignKey("shipments.id", ondelete="CASCADE"))

    requirement_type: Mapped[str] = mapped_column(String(24))
    document_type_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("document_types.id", ondelete="RESTRICT")
    )

    title: Mapped[str] = mapped_column(String(180))
    description: Mapped[str | None] = mapped_column(Text)

    # Quién debe actuar. El dashboard del cliente solo muestra los suyos: un
    # packing list pendiente es de Operaciones, no del cliente (ADR-0003).
    required_from: Mapped[str] = mapped_column(String(16))

    status: Mapped[str] = mapped_column(String(20))

    # Si es true, bloquea la transición declarada en el tipo de documento.
    # Un requisito informativo no impide despachar.
    blocks_dispatch: Mapped[bool] = mapped_column(server_default=text("true"))

    due_at: Mapped[datetime | None]

    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    completed_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    verified_document_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="RESTRICT")
    )
    reviewed_document_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="RESTRICT")
    )

    # Motivo obligatorio al exonerar (WAIVED) o rechazar (REJECTED).
    resolution_reason: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    completed_at: Mapped[datetime | None]

    __table_args__ = (
        CheckConstraint(
            "requirement_type IN ('DOCUMENT', 'INFORMATION', 'PAYMENT', 'ACTION')",
            name="requirement_type_valido",
        ),
        CheckConstraint("required_from IN ('CLIENT', 'STAFF')", name="required_from_valido"),
        # Un requisito documental sin tipo de documento no se puede satisfacer:
        # nadie sabría qué archivo pide.
        CheckConstraint(
            "requirement_type <> 'DOCUMENT' OR document_type_id IS NOT NULL",
            name="documental_exige_tipo",
        ),
        # ADR-0003: cada tipo usa su propio juego de estados. El CHECK impide
        # que un requisito de pago quede en `VERIFIED`, que no significa nada ahí.
        CheckConstraint(
            "(requirement_type = 'DOCUMENT' AND status IN "
            "  ('PENDING', 'UPLOADED', 'VERIFIED', 'REJECTED', 'NOT_APPLICABLE', "
            "   'WAIVED', 'CANCELLED')) "
            "OR (requirement_type <> 'DOCUMENT' AND status IN "
            "  ('OPEN', 'FULFILLED', 'WAIVED', 'CANCELLED'))",
            name="status_valido_para_el_tipo",
        ),
        # Exonerar y rechazar exigen explicación: sin ella la auditoría no sirve.
        CheckConstraint(
            "status NOT IN ('WAIVED', 'REJECTED') OR resolution_reason IS NOT NULL",
            name="exonerar_o_rechazar_exige_motivo",
        ),
        # Índice parcial: el dashboard consulta lo pendiente, que es una
        # fracción del total con el tiempo.
        Index(
            "ix_shipment_requirements_pendientes",
            "shipment_id",
            "required_from",
            postgresql_where=text("status IN ('OPEN', 'PENDING', 'UPLOADED', 'REJECTED')"),
        ),
    )


class DisputeStatus(StrEnum):
    OPEN = "OPEN"
    # Se sostiene la entrega tal cual quedó registrada.
    RESOLVED_CONFIRMED = "RESOLVED_CONFIRMED"
    # Dispara la reversión de DELIVERED — sujeta a las mismas reglas de
    # siempre (SUPER_ADMIN, motivo obligatorio, ver `service.transicionar`).
    RESOLVED_REVERTED = "RESOLVED_REVERTED"


class DeliveryDispute(Base):
    """Inconformidad del cliente con una entrega ya marcada (ADR-0006).

    Reportarla NO cambia el estado por sí sola — es Operaciones quien
    resuelve, y solo `RESOLVED_REVERTED` toca `shipments.current_status_code`
    (vía el motor de transiciones normal, no un UPDATE propio acá). Mientras
    está `OPEN`, bloquea el archivado de la carga (ADR-0007).
    """

    __tablename__ = "delivery_disputes"

    id: Mapped[UUIDPk]
    shipment_id: Mapped[UUID] = mapped_column(ForeignKey("shipments.id", ondelete="RESTRICT"))
    raised_by_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))

    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), server_default=text("'OPEN'"))

    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    resolved_at: Mapped[datetime | None]
    resolved_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('OPEN', 'RESOLVED_CONFIRMED', 'RESOLVED_REVERTED')",
            name="dispute_status_valido",
        ),
        CheckConstraint(
            "status = 'OPEN' OR (resolved_at IS NOT NULL AND resolved_by_user_id IS NOT NULL)",
            name="dispute_resuelta_tiene_fecha_y_actor",
        ),
        # Una carga no tiene dos inconformidades abiertas a la vez — hay que
        # resolver la que ya existe antes de reportar otra.
        Index(
            "ix_delivery_disputes_una_abierta_por_carga",
            "shipment_id",
            unique=True,
            postgresql_where=text("status = 'OPEN'"),
        ),
        # El barrido de archivado (ADR-0007) consulta "¿hay alguna abierta
        # para esta carga?" — el mismo índice de arriba ya lo resuelve barato,
        # pero se nombra acá para que quede documentada la razón de ambos.
    )

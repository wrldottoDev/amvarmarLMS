"""peso canonico y busqueda de referencias

Revision ID: 4c2a6e91d7b8
Revises: f9329a7c5d0f
Create Date: 2026-08-26 16:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4c2a6e91d7b8"
down_revision: str | Sequence[str] | None = "f9329a7c5d0f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conexion = op.get_bind()
    courier = conexion.exec_driver_sql(
        "SELECT count(*) FROM shipments WHERE transport_mode = 'COURIER'"
    ).scalar_one()
    if courier:
        raise RuntimeError(
            f"Hay {courier} cargas con transport_mode=COURIER. Asigne SEA, AIR o LAND "
            "antes de aplicar esta migración; no existe una traducción automática segura."
        )

    op.add_column("shipments", sa.Column("weight_source_unit", sa.String(2), nullable=True))
    op.create_check_constraint(
        op.f("ck_shipments_peso_fuente_valido"),
        "shipments",
        "weight_source_unit IS NULL OR (weight_source_unit IN ('KG', 'LB') "
        "AND weight_kg > 0 AND weight_lb > 0)",
    )

    op.drop_constraint(op.f("ck_shipments_transport_mode_valido"), "shipments", type_="check")
    op.create_check_constraint(
        op.f("ck_shipments_transport_mode_valido"),
        "shipments",
        "transport_mode IS NULL OR transport_mode IN ('SEA', 'AIR', 'LAND')",
    )

    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.add_column(
        "shipment_references",
        sa.Column("facility_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "shipment_references",
        sa.Column("normalized_value", sa.String(180), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_shipment_references_facility_id_facilities"),
        "shipment_references",
        "facilities",
        ["facility_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # Las cargas creadas antes de modelar facilities ya tienen ubicación y WR,
    # pero `origin_facility_id` quedó NULL. Solo se puede completar sin
    # inventar cuando la ubicación tiene exactamente una facility activa que
    # emite Warehouse Receipt. Cero o varias candidatas quedan intactas y la
    # validación de abajo detiene la migración con un diagnóstico explícito.
    op.execute("""
        WITH candidatas AS (
            SELECT s.id AS shipment_id,
                   (array_agg(f.id ORDER BY f.facility_code))[1] AS facility_id
            FROM shipments s
            JOIN shipment_references r
              ON r.shipment_id = s.id AND r.reference_type = 'WR'
            JOIN facilities f
              ON f.location_id = s.origin_location_id
             AND f.uses_warehouse_receipt
             AND f.is_active
            WHERE s.origin_facility_id IS NULL
            GROUP BY s.id
            HAVING count(f.id) = 1
        )
        UPDATE shipments s
        SET origin_facility_id = c.facility_id
        FROM candidatas c
        WHERE s.id = c.shipment_id
    """)
    op.execute("""
        UPDATE shipment_references r
        SET normalized_value = regexp_replace(upper(btrim(r.value)), '[^A-Z0-9]+', '', 'g'),
            facility_id = CASE WHEN r.reference_type = 'WR'
                               THEN s.origin_facility_id ELSE NULL END
        FROM shipments s
        WHERE s.id = r.shipment_id
    """)
    invalidos = conexion.exec_driver_sql("""
        SELECT count(*)
        FROM shipment_references r
        LEFT JOIN facilities f ON f.id = r.facility_id
        WHERE r.reference_type = 'WR'
          AND (r.facility_id IS NULL OR NOT COALESCE(f.uses_warehouse_receipt, false))
    """).scalar_one()
    if invalidos:
        raise RuntimeError(
            f"Hay {invalidos} referencias WR sin una facility emisora válida. "
            "Corrija origin_facility_id antes de volver a migrar."
        )

    op.alter_column("shipment_references", "normalized_value", nullable=False)
    op.create_check_constraint(
        op.f("ck_shipment_references_wr_exige_facility"),
        "shipment_references",
        "reference_type <> 'WR' OR facility_id IS NOT NULL",
    )
    op.create_index(
        "uq_shipment_references_wr_facility",
        "shipment_references",
        ["facility_id", "normalized_value"],
        unique=True,
        postgresql_where=sa.text("reference_type = 'WR'"),
    )

    # La base deriva y valida la metadata incluso para migradores y SQL directo.
    op.execute("""
        CREATE FUNCTION preparar_referencia_carga() RETURNS trigger AS $$
        DECLARE
            bodega uuid;
            usa_wr boolean;
        BEGIN
            NEW.normalized_value := regexp_replace(
                upper(btrim(NEW.value)), '[^A-Z0-9]+', '', 'g'
            );
            IF NEW.reference_type = 'WR' THEN
                SELECT s.origin_facility_id, COALESCE(f.uses_warehouse_receipt, false)
                INTO bodega, usa_wr
                FROM shipments s
                LEFT JOIN facilities f ON f.id = s.origin_facility_id
                WHERE s.id = NEW.shipment_id;
                IF bodega IS NULL OR NOT usa_wr THEN
                    RAISE EXCEPTION
                        'El WR requiere una facility de origen que emita Warehouse Receipt.'
                        USING ERRCODE = 'check_violation';
                END IF;
                NEW.facility_id := bodega;
            ELSE
                NEW.facility_id := NULL;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER trg_preparar_referencia_carga
        BEFORE INSERT OR UPDATE OF shipment_id, reference_type, value, facility_id
        ON shipment_references
        FOR EACH ROW EXECUTE FUNCTION preparar_referencia_carga()
    """)
    op.execute("""
        CREATE FUNCTION propagar_facility_a_wr() RETURNS trigger AS $$
        BEGIN
            IF NEW.origin_facility_id IS DISTINCT FROM OLD.origin_facility_id THEN
                UPDATE shipment_references
                SET facility_id = NEW.origin_facility_id
                WHERE shipment_id = NEW.id AND reference_type = 'WR';
            END IF;
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER trg_propagar_facility_a_wr
        AFTER UPDATE OF origin_facility_id ON shipments
        FOR EACH ROW EXECUTE FUNCTION propagar_facility_a_wr()
    """)
    op.create_index(
        "ix_shipments_numero_trgm",
        "shipments",
        ["shipment_number"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"shipment_number": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_shipments_shipper_trgm",
        "shipments",
        ["shipper"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"shipper": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_shipments_carrier_trgm",
        "shipments",
        ["carrier"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"carrier": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_shipment_references_value_trgm",
        "shipment_references",
        ["value"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"value": "gin_trgm_ops"},
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_propagar_facility_a_wr ON shipments")
    op.execute("DROP FUNCTION IF EXISTS propagar_facility_a_wr()")
    op.execute("DROP TRIGGER IF EXISTS trg_preparar_referencia_carga ON shipment_references")
    op.execute("DROP FUNCTION IF EXISTS preparar_referencia_carga()")
    op.drop_index("uq_shipment_references_wr_facility", table_name="shipment_references")
    op.drop_constraint(
        op.f("ck_shipment_references_wr_exige_facility"),
        "shipment_references",
        type_="check",
    )
    op.drop_constraint(
        op.f("fk_shipment_references_facility_id_facilities"),
        "shipment_references",
        type_="foreignkey",
    )
    op.drop_column("shipment_references", "normalized_value")
    op.drop_column("shipment_references", "facility_id")
    op.drop_index("ix_shipment_references_value_trgm", table_name="shipment_references")
    op.drop_index("ix_shipments_carrier_trgm", table_name="shipments")
    op.drop_index("ix_shipments_shipper_trgm", table_name="shipments")
    op.drop_index("ix_shipments_numero_trgm", table_name="shipments")
    op.drop_constraint(op.f("ck_shipments_transport_mode_valido"), "shipments", type_="check")
    op.create_check_constraint(
        op.f("ck_shipments_transport_mode_valido"),
        "shipments",
        "transport_mode IS NULL OR transport_mode IN ('SEA', 'AIR', 'LAND', 'COURIER')",
    )
    op.drop_constraint(op.f("ck_shipments_peso_fuente_valido"), "shipments", type_="check")
    op.drop_column("shipments", "weight_source_unit")

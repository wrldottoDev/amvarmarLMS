"""contexto y seguridad documental

Revision ID: 7a31c4f09d22
Revises: 4c2a6e91d7b8
Create Date: 2026-08-26 17:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "7a31c4f09d22"
down_revision: str | Sequence[str] | None = "4c2a6e91d7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conexion = op.get_bind()

    op.drop_constraint(
        op.f("ck_document_types_provided_by_valido"), "document_types", type_="check"
    )
    op.add_column("document_types", sa.Column("context", sa.String(16), nullable=True))
    op.add_column(
        "document_types",
        sa.Column("issued_by_options", postgresql.ARRAY(sa.String(16)), nullable=True),
    )
    op.execute("""
        UPDATE document_types
        SET context = CASE WHEN upper(code) = 'BL' THEN 'DISPATCH' ELSE 'SHIPMENT' END,
            provided_by = CASE WHEN upper(code) IN ('COMMERCIAL_INVOICE', 'SLI')
                               THEN 'CLIENT_OR_STAFF' ELSE provided_by END,
            issued_by_options = CASE upper(code)
                WHEN 'COMMERCIAL_INVOICE' THEN ARRAY['PROVIDER','CLIENT']::varchar[]
                WHEN 'SLI' THEN ARRAY['PROVIDER','CLIENT']::varchar[]
                WHEN 'PACKING_LIST' THEN ARRAY['PROVIDER']::varchar[]
                WHEN 'BL' THEN ARRAY['CARRIER','AMVARMAR']::varchar[]
                WHEN 'SPECIAL_PERMIT' THEN ARRAY['CLIENT','AUTHORITY']::varchar[]
                WHEN 'WAREHOUSE_RECEIPT' THEN ARRAY['AMVARMAR']::varchar[]
                WHEN 'PROOF_OF_DELIVERY' THEN ARRAY['AMVARMAR','CARRIER']::varchar[]
                ELSE ARRAY['OTHER']::varchar[]
            END
    """)
    op.alter_column("document_types", "context", nullable=False)
    op.alter_column("document_types", "issued_by_options", nullable=False)
    op.create_check_constraint(
        op.f("ck_document_types_provided_by_valido"),
        "document_types",
        "provided_by IN ('CLIENT', 'STAFF', 'CLIENT_OR_STAFF')",
    )
    op.create_check_constraint(
        op.f("ck_document_types_contexto_valido"),
        "document_types",
        "context IN ('SHIPMENT', 'DISPATCH')",
    )
    op.create_check_constraint(
        op.f("ck_document_types_al_menos_un_emisor"),
        "document_types",
        "cardinality(issued_by_options) > 0",
    )

    op.add_column("documents", sa.Column("issued_by", sa.String(16), nullable=True))
    op.add_column("documents", sa.Column("invalidated_by", sa.Uuid(), nullable=True))
    op.add_column("documents", sa.Column("invalidation_reason", sa.Text(), nullable=True))
    op.execute("UPDATE documents SET issued_by = 'OTHER'")
    op.execute("""
        UPDATE documents
        SET invalidation_reason = 'Invalidado antes de registrar motivos estructurados.'
        WHERE deleted_at IS NOT NULL
    """)
    op.alter_column("documents", "issued_by", nullable=False)
    op.create_foreign_key(
        op.f("fk_documents_invalidated_by_users"),
        "documents",
        "users",
        ["invalidated_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        op.f("ck_documents_issued_by_valido"),
        "documents",
        "issued_by IN ('PROVIDER','CLIENT','AMVARMAR','CARRIER','AUTHORITY','OTHER')",
    )
    op.create_check_constraint(
        op.f("ck_documents_invalidacion_exige_motivo"),
        "documents",
        "deleted_at IS NULL OR invalidation_reason IS NOT NULL",
    )

    op.add_column(
        "shipment_requirements", sa.Column("verified_document_id", sa.Uuid(), nullable=True)
    )
    op.add_column(
        "shipment_requirements", sa.Column("reviewed_document_id", sa.Uuid(), nullable=True)
    )
    for columna in ("verified_document_id", "reviewed_document_id"):
        op.create_foreign_key(
            op.f(f"fk_shipment_requirements_{columna}_documents"),
            "shipment_requirements",
            "documents",
            [columna],
            ["id"],
            ondelete="RESTRICT",
        )

    # Corrige la fuga histórica: un documento de despacho no conserva además
    # el enlace artificial a la primera carga del lote.
    op.execute("""
        DELETE FROM shipment_documents sd
        USING dispatch_documents dd
        WHERE dd.document_id = sd.document_id
    """)
    bl_huerfanos = conexion.exec_driver_sql("""
        SELECT count(*)
        FROM shipment_documents sd
        JOIN document_types dt ON dt.id = sd.document_type_id
        WHERE upper(dt.code) = 'BL'
    """).scalar_one()
    if bl_huerfanos:
        raise RuntimeError(
            f"Hay {bl_huerfanos} BL ligados solo a cargas y no se puede inventar su despacho. "
            "Relaciónelos con dispatch_documents antes de aplicar la migración."
        )

    op.execute("""
        CREATE FUNCTION validar_contexto_enlace_documento() RETURNS trigger AS $$
        DECLARE contexto varchar;
        BEGIN
            PERFORM pg_advisory_xact_lock(hashtextextended(NEW.document_id::text, 0));
            SELECT context INTO contexto FROM document_types WHERE id = NEW.document_type_id;
            IF TG_TABLE_NAME = 'shipment_documents' THEN
                IF contexto <> 'SHIPMENT' OR EXISTS (
                    SELECT 1 FROM dispatch_documents WHERE document_id = NEW.document_id
                ) THEN
                    RAISE EXCEPTION 'Documento o tipo no válido para una carga.'
                        USING ERRCODE = 'check_violation';
                END IF;
            ELSE
                IF contexto <> 'DISPATCH' OR EXISTS (
                    SELECT 1 FROM shipment_documents WHERE document_id = NEW.document_id
                ) THEN
                    RAISE EXCEPTION 'Documento o tipo no válido para un despacho.'
                        USING ERRCODE = 'check_violation';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER trg_contexto_documento_carga
        BEFORE INSERT OR UPDATE ON shipment_documents
        FOR EACH ROW EXECUTE FUNCTION validar_contexto_enlace_documento()
    """)
    op.execute("""
        CREATE TRIGGER trg_contexto_documento_despacho
        BEFORE INSERT OR UPDATE ON dispatch_documents
        FOR EACH ROW EXECUTE FUNCTION validar_contexto_enlace_documento()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_contexto_documento_despacho ON dispatch_documents")
    op.execute("DROP TRIGGER IF EXISTS trg_contexto_documento_carga ON shipment_documents")
    op.execute("DROP FUNCTION IF EXISTS validar_contexto_enlace_documento()")
    for columna in ("reviewed_document_id", "verified_document_id"):
        op.drop_constraint(
            op.f(f"fk_shipment_requirements_{columna}_documents"),
            "shipment_requirements",
            type_="foreignkey",
        )
        op.drop_column("shipment_requirements", columna)
    op.drop_constraint(
        op.f("ck_documents_invalidacion_exige_motivo"), "documents", type_="check"
    )
    op.drop_constraint(op.f("ck_documents_issued_by_valido"), "documents", type_="check")
    op.drop_constraint(
        op.f("fk_documents_invalidated_by_users"), "documents", type_="foreignkey"
    )
    op.drop_column("documents", "invalidation_reason")
    op.drop_column("documents", "invalidated_by")
    op.drop_column("documents", "issued_by")
    op.drop_constraint(
        op.f("ck_document_types_al_menos_un_emisor"), "document_types", type_="check"
    )
    op.drop_constraint(op.f("ck_document_types_contexto_valido"), "document_types", type_="check")
    op.drop_constraint(
        op.f("ck_document_types_provided_by_valido"), "document_types", type_="check"
    )
    op.execute("""
        UPDATE document_types
        SET provided_by = 'CLIENT'
        WHERE provided_by = 'CLIENT_OR_STAFF'
    """)
    op.create_check_constraint(
        op.f("ck_document_types_provided_by_valido"),
        "document_types",
        "provided_by IN ('CLIENT', 'STAFF')",
    )
    op.drop_column("document_types", "issued_by_options")
    op.drop_column("document_types", "context")

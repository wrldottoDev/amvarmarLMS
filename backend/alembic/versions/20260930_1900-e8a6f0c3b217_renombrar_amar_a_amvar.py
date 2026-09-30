"""renombrar la referencia AMAR a AMVAR

Revision ID: e8a6f0c3b217
Revises: d2f7b8c19a34
Create Date: 2026-09-30 19:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e8a6f0c3b217"
down_revision: str | Sequence[str] | None = "d2f7b8c19a34"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_shipment_references_reference_type_valido"),
        "shipment_references",
        type_="check",
    )
    op.execute(
        "UPDATE shipment_references SET reference_type = 'AMVAR' WHERE reference_type = 'AMAR'"
    )
    op.execute("""
        UPDATE column_preferences
        SET columnas = replace(columnas::text, '"amar"', '"amvar"')::jsonb,
            updated_at = now()
        WHERE columnas ? 'amar'
    """)
    op.create_check_constraint(
        op.f("ck_shipment_references_reference_type_valido"),
        "shipment_references",
        "reference_type IN ('INVOICE', 'WR', 'PO', 'TRACKING', 'CONTAINER', 'BL', 'AMVAR', 'OTHER')",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_shipment_references_reference_type_valido"),
        "shipment_references",
        type_="check",
    )
    op.execute(
        "UPDATE shipment_references SET reference_type = 'AMAR' WHERE reference_type = 'AMVAR'"
    )
    op.execute("""
        UPDATE column_preferences
        SET columnas = replace(columnas::text, '"amvar"', '"amar"')::jsonb,
            updated_at = now()
        WHERE columnas ? 'amvar'
    """)
    op.create_check_constraint(
        op.f("ck_shipment_references_reference_type_valido"),
        "shipment_references",
        "reference_type IN ('INVOICE', 'WR', 'PO', 'TRACKING', 'CONTAINER', 'BL', 'AMAR', 'OTHER')",
    )

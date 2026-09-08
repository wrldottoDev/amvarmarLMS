"""inconformidad de entrega

Revision ID: 98958af7401b
Revises: 2b2a1537eb51
Create Date: 2026-09-05 23:11:56.317119
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "98958af7401b"
down_revision: str | Sequence[str] | None = "2b2a1537eb51"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "delivery_disputes",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("shipment_id", sa.Uuid(), nullable=False),
        sa.Column("raised_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default=sa.text("'OPEN'"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by_user_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "status = 'OPEN' OR (resolved_at IS NOT NULL AND resolved_by_user_id IS NOT NULL)",
            name=op.f("ck_delivery_disputes_dispute_resuelta_tiene_fecha_y_actor"),
        ),
        sa.CheckConstraint(
            "status IN ('OPEN', 'RESOLVED_CONFIRMED', 'RESOLVED_REVERTED')",
            name=op.f("ck_delivery_disputes_dispute_status_valido"),
        ),
        sa.ForeignKeyConstraint(
            ["raised_by_user_id"],
            ["users.id"],
            name=op.f("fk_delivery_disputes_raised_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by_user_id"],
            ["users.id"],
            name=op.f("fk_delivery_disputes_resolved_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["shipment_id"],
            ["shipments.id"],
            name=op.f("fk_delivery_disputes_shipment_id_shipments"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_delivery_disputes")),
    )
    op.create_index(
        "ix_delivery_disputes_una_abierta_por_carga",
        "delivery_disputes",
        ["shipment_id"],
        unique=True,
        postgresql_where=sa.text("status = 'OPEN'"),
    )


def downgrade() -> None:
    op.drop_index("ix_delivery_disputes_una_abierta_por_carga", table_name="delivery_disputes")
    op.drop_table("delivery_disputes")

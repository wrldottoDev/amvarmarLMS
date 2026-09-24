"""propuestas de accion del copiloto

Revision ID: 2b2a1537eb51
Revises: d8e4f7a12c30
Create Date: 2026-09-02 19:46:42.321785
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "2b2a1537eb51"
down_revision: str | Sequence[str] | None = "d8e4f7a12c30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "copilot_action_proposals",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=True),
        sa.Column("action_code", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "resource_versions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'PENDING'"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "action_code IN ('procesar_factura_ocr', 'crear_prealerta_borrador')",
            name=op.f("ck_copilot_action_proposals_action_code_valido"),
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'CONFIRMED', 'REJECTED', 'EXPIRED', 'FAILED')",
            name=op.f("ck_copilot_action_proposals_status_valido"),
        ),
        sa.CheckConstraint(
            "status = 'PENDING' OR resolved_at IS NOT NULL",
            name=op.f("ck_copilot_action_proposals_resuelta_tiene_fecha"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_copilot_action_proposals_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_copilot_action_proposals_company_id_companies"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_copilot_action_proposals")),
    )
    op.create_index(
        "ix_copilot_proposals_creador_estado",
        "copilot_action_proposals",
        ["created_by", "status"],
        unique=False,
        postgresql_where=sa.text("status = 'PENDING'"),
    )
    op.create_index(
        "ix_copilot_proposals_vencimiento",
        "copilot_action_proposals",
        ["expires_at"],
        unique=False,
        postgresql_where=sa.text("status = 'PENDING'"),
    )


def downgrade() -> None:
    op.drop_index("ix_copilot_proposals_vencimiento", table_name="copilot_action_proposals")
    op.drop_index("ix_copilot_proposals_creador_estado", table_name="copilot_action_proposals")
    op.drop_table("copilot_action_proposals")

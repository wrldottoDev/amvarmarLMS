"""trabajos de exportacion documental

Revision ID: d8e4f7a12c30
Revises: 6f0f9c2be8d1
Create Date: 2026-08-26 19:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d8e4f7a12c30"
down_revision: str | Sequence[str] | None = "6f0f9c2be8d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "document_export_jobs",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("resource_type", sa.String(16), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("source_fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("storage_key", sa.String(500), nullable=True),
        sa.Column("result_name", sa.String(255), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("started_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "resource_type IN ('SHIPMENT', 'DISPATCH')",
            name=op.f("ck_document_export_jobs_recurso_exportable_valido"),
        ),
        sa.CheckConstraint(
            "kind IN ('ALL_DOCUMENTS', 'BLS')",
            name=op.f("ck_document_export_jobs_tipo_exportacion_valido"),
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'PROCESSING', 'READY', 'FAILED', 'EXPIRED')",
            name=op.f("ck_document_export_jobs_estado_exportacion_valido"),
        ),
        sa.CheckConstraint(
            "status <> 'READY' OR (storage_key IS NOT NULL AND result_name IS NOT NULL "
            "AND size_bytes > 0 AND length(sha256) = 64)",
            name=op.f("ck_document_export_jobs_exportacion_lista_con_resultado"),
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["users.id"],
            name=op.f("fk_document_export_jobs_requested_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_document_export_jobs_company_id_companies"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_export_jobs")),
        sa.UniqueConstraint(
            "requested_by",
            "resource_type",
            "resource_id",
            "kind",
            "source_fingerprint",
            name="uq_document_export_jobs_solicitud_fuente",
        ),
    )
    op.create_index(
        "ix_document_export_jobs_empresa_recurso",
        "document_export_jobs",
        ["company_id", "resource_type", "resource_id", sa.literal_column("created_at DESC")],
    )
    op.create_index(
        "ix_document_export_jobs_estado_expira",
        "document_export_jobs",
        ["status", "expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_document_export_jobs_estado_expira", table_name="document_export_jobs"
    )
    op.drop_index(
        "ix_document_export_jobs_empresa_recurso", table_name="document_export_jobs"
    )
    op.drop_table("document_export_jobs")

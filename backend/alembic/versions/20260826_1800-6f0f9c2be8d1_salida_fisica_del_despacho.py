"""salida fisica del despacho

Revision ID: 6f0f9c2be8d1
Revises: 7a31c4f09d22
Create Date: 2026-08-26 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6f0f9c2be8d1"
down_revision: str | Sequence[str] | None = "7a31c4f09d22"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "dispatch_requests",
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("""
        UPDATE dispatch_requests
        SET dispatched_at = COALESCE(completed_at, updated_at)
        WHERE status IN ('DISPATCHED', 'COMPLETED')
    """)


def downgrade() -> None:
    op.drop_column("dispatch_requests", "dispatched_at")

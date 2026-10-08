"""datos aduaneros de tránsito

Revision ID: d2f7b8c19a34
Revises: c7a91e2d4f60
Create Date: 2026-09-30 17:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d2f7b8c19a34"
down_revision: str | Sequence[str] | None = "c7a91e2d4f60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("shipments", sa.Column("tariff_code", sa.String(length=40), nullable=True))
    op.create_check_constraint(
        "partida_arancelaria_valida",
        "shipments",
        "tariff_code IS NULL OR tariff_code ~ '^[0-9]{1,40}$'",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_shipments_partida_arancelaria_valida"), "shipments", type_="check")
    op.drop_column("shipments", "tariff_code")

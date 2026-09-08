"""outbox: status explícito y clave de deduplicación

Revision ID: 99010939fc42
Revises: 62dae444e5c2
Create Date: 2026-08-25 09:28:33.127327

La tabla existe desde la Fase 1 (ADR-0008) para no perder los eventos de
seguridad nacidos antes del worker. El Paso 4.1 le agrega lo que el worker
necesita: un estado explícito y una clave que impide escribir dos veces el
mismo hecho de negocio.

`status` NO tiene valor PROCESSING: el worker reclama con
`FOR UPDATE SKIP LOCKED` y PostgreSQL suelta ese lock solo si el proceso muere.
Un PROCESSING persistido sobreviviría a la muerte del worker y dejaría el
evento trabado, que es justo lo que este paso debe evitar.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "99010939fc42"
down_revision: str | Sequence[str] | None = "62dae444e5c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "outbox_events",
        sa.Column(
            "status",
            sa.String(length=12),
            server_default=sa.text("'PENDING'"),
            nullable=False,
        ),
    )
    op.add_column("outbox_events", sa.Column("dedup_key", sa.String(length=200), nullable=True))

    # Backfill ANTES de los CHECK: las filas que la Fase 1 ya procesó tienen
    # `processed_at`, y con el default 'PENDING' violarían la coherencia entre
    # estado y fecha.
    op.execute("UPDATE outbox_events SET status = 'DONE' WHERE processed_at IS NOT NULL")

    op.create_index(
        "uq_outbox_events_dedup",
        "outbox_events",
        ["dedup_key"],
        unique=True,
        postgresql_where=sa.text("dedup_key IS NOT NULL"),
    )
    op.create_check_constraint(
        op.f("ck_outbox_events_outbox_procesado_tiene_fecha"),
        "outbox_events",
        "(status = 'PENDING') = (processed_at IS NULL)",
    )
    op.create_check_constraint(
        op.f("ck_outbox_events_outbox_status_valido"),
        "outbox_events",
        "status IN ('PENDING', 'DONE', 'FAILED')",
    )

    # El índice del worker pasa a filtrar por estado: un evento agotado (FAILED)
    # tampoco tiene `processed_at`, así que el filtro viejo lo habría seguido
    # devolviendo para siempre.
    op.drop_index("ix_outbox_events_pendientes", table_name="outbox_events")
    op.create_index(
        "ix_outbox_events_pendientes",
        "outbox_events",
        ["available_at", "created_at"],
        postgresql_where=sa.text("status = 'PENDING'"),
    )


def downgrade() -> None:
    op.drop_index("ix_outbox_events_pendientes", table_name="outbox_events")
    op.create_index(
        "ix_outbox_events_pendientes",
        "outbox_events",
        ["available_at", "created_at"],
        postgresql_where=sa.text("processed_at IS NULL"),
    )
    op.drop_constraint(
        op.f("ck_outbox_events_outbox_status_valido"), "outbox_events", type_="check"
    )
    op.drop_constraint(
        op.f("ck_outbox_events_outbox_procesado_tiene_fecha"), "outbox_events", type_="check"
    )
    op.drop_index(
        "uq_outbox_events_dedup",
        table_name="outbox_events",
        postgresql_where=sa.text("dedup_key IS NOT NULL"),
    )
    op.drop_column("outbox_events", "dedup_key")
    op.drop_column("outbox_events", "status")

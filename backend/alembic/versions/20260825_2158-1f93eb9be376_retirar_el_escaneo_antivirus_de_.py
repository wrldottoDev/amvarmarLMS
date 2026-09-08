"""retirar el escaneo antivirus de documentos

AMVARMAR decidió no usar antivirus sobre los documentos subidos. Se quita el
control entero en vez de dejarlo apagado: una columna `scan_status` que nadie
consulta parece protección y no la es, y el próximo que lea el código creería
que los archivos se revisan.

Lo que queda es `upload_status`, que es otra cosa: el estado técnico de la
subida. Un documento es descargable cuando terminó de subirse.

Revision ID: 1f93eb9be376
Revises: d98e024bf210
Create Date: 2026-08-25 21:58:44.532414
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "1f93eb9be376"
down_revision: str | Sequence[str] | None = "d98e024bf210"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Los documentos que estaban esperando al escáner pasan a disponibles. Sin
    # esto quedarían en PROCESSING para siempre, porque el worker que los
    # movía ya no existe: 277 documentos migrados del sistema viejo estaban
    # exactamente en ese estado.
    op.execute("""
        UPDATE documents
        SET upload_status = 'READY'
        WHERE upload_status = 'PROCESSING' AND deleted_at IS NULL
    """)

    op.drop_constraint(op.f("ck_documents_scan_status_valido"), "documents", type_="check")
    op.drop_column("documents", "scan_status")
    op.drop_column("documents", "scanned_at")


def downgrade() -> None:
    # `server_default` y después quitarlo: sin eso, agregar una columna NOT NULL
    # a una tabla con filas falla, y esta las tiene.
    op.add_column(
        "documents",
        sa.Column("scanned_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column(
            "scan_status",
            sa.VARCHAR(length=20),
            nullable=False,
            server_default="PENDING",
        ),
    )
    op.alter_column("documents", "scan_status", server_default=None)

    # Lo que ya estaba disponible sigue estándolo. Marcarlo todo PENDING dejaría
    # el expediente entero sin descargar de golpe, que sería una sorpresa peor
    # que la de volver atrás.
    op.execute("""
        UPDATE documents SET scan_status = 'CLEAN', scanned_at = now()
        WHERE upload_status = 'READY'
    """)

    op.create_check_constraint(
        op.f("ck_documents_scan_status_valido"),
        "documents",
        "scan_status IN ('PENDING', 'CLEAN', 'INFECTED', 'FAILED')",
    )

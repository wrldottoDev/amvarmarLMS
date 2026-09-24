"""extensiones pgcrypto y citext

Base del esquema (sección 3.1 del documento de arquitectura):
- pgcrypto provee gen_random_uuid(), usada como server_default de toda PK UUID.
- citext provee el tipo CITEXT, usado en users.email para que la unicidad sea
  insensible a mayúsculas sin depender de lower() en cada consulta.

Revision ID: d62ea549f5a9
Revises:
Create Date: 2026-08-24 11:23

"""

from collections.abc import Sequence

from alembic import op

revision: str = "d62ea549f5a9"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS citext")
    op.execute("DROP EXTENSION IF EXISTS pgcrypto")

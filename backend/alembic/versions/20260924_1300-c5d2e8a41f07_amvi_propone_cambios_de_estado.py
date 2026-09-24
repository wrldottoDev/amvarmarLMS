"""amvi propone cambios de estado

Revision ID: c5d2e8a41f07
Revises: a3f1c92e4b70
Create Date: 2026-09-24 13:00:00.000000

ADR-0012, enmienda 2026-09-24. `proponer_cambio_estado` es una acción de
escritura nueva: su propuesta tiene que caber en el CHECK de `action_code`,
que es la última defensa si alguien inserta a mano. Se reemplaza el
constraint entero porque un CHECK no se altera en sitio.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c5d2e8a41f07"
down_revision: str | Sequence[str] | None = "a3f1c92e4b70"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLA = "copilot_action_proposals"
_NOMBRE = "ck_copilot_action_proposals_action_code_valido"


def upgrade() -> None:
    op.drop_constraint(op.f(_NOMBRE), _TABLA, type_="check")
    op.create_check_constraint(
        # `op.f`: el nombre ya es el final; sin esto la convención de nombres
        # de `MetaData` lo vuelve a prefijar y el constraint no se encuentra.
        op.f(_NOMBRE),
        _TABLA,
        "action_code IN ('procesar_factura_ocr', 'crear_prealerta_borrador', "
        "'proponer_cambio_estado')",
    )


def downgrade() -> None:
    # Una propuesta de cambio de estado no cabe en el constraint viejo: se
    # borran antes de restaurarlo. Son efímeras (vencen a los 30 minutos).
    op.execute(f"DELETE FROM {_TABLA} WHERE action_code = 'proponer_cambio_estado'")
    op.drop_constraint(op.f(_NOMBRE), _TABLA, type_="check")
    op.create_check_constraint(
        # `op.f`: el nombre ya es el final; sin esto la convención de nombres
        # de `MetaData` lo vuelve a prefijar y el constraint no se encuentra.
        op.f(_NOMBRE),
        _TABLA,
        "action_code IN ('procesar_factura_ocr', 'crear_prealerta_borrador')",
    )

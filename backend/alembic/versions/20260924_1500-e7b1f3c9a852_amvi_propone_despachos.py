"""amvi propone despachos

Revision ID: e7b1f3c9a852
Revises: c5d2e8a41f07
Create Date: 2026-09-24 15:00:00.000000

ADR-0017, enmienda 2026-09-24. `proponer_despacho` es la herramienta con la
que AMVI le prepara al cliente su solicitud de despacho: su propuesta tiene que
caber en el CHECK de `action_code`.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e7b1f3c9a852"
down_revision: str | Sequence[str] | None = "c5d2e8a41f07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLA = "copilot_action_proposals"
# `op.f`: el nombre ya es el final; sin esto la convención de nombres de
# `MetaData` lo vuelve a prefijar y el constraint no se encuentra.
_NOMBRE = "ck_copilot_action_proposals_action_code_valido"


def _reemplazar(valores: str) -> None:
    op.drop_constraint(op.f(_NOMBRE), _TABLA, type_="check")
    op.create_check_constraint(op.f(_NOMBRE), _TABLA, f"action_code IN ({valores})")


def upgrade() -> None:
    _reemplazar(
        "'procesar_factura_ocr', 'crear_prealerta_borrador', "
        "'proponer_cambio_estado', 'proponer_despacho'"
    )


def downgrade() -> None:
    # Las propuestas vencen a los 30 minutos: borrarlas no pierde nada que valga.
    op.execute(f"DELETE FROM {_TABLA} WHERE action_code = 'proponer_despacho'")
    _reemplazar("'procesar_factura_ocr', 'crear_prealerta_borrador', 'proponer_cambio_estado'")

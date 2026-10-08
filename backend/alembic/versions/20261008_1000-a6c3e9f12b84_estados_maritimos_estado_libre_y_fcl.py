"""estados marítimos, cambio de estado libre y tipo FCL/LCL

Revision ID: a6c3e9f12b84
Revises: e8a6f0c3b217
Create Date: 2026-10-08 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a6c3e9f12b84"
down_revision: str | Sequence[str] | None = "e8a6f0c3b217"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Espejo de `shipments/catalog.py` en la fecha de esta migración: el seed se
# encarga de mantenerlo después, pero un despliegue que solo corre Alembic
# tiene que quedar con los estados y transiciones nuevos.
_ESTADOS = (
    ("BOOKING_ASSIGNED", "Booking asignado", "PRE_ARRIVAL", 15, False),
    ("TRANSSHIPMENT", "Transbordo", "PRE_ARRIVAL", 25, False),
    ("AT_DESTINATION", "En destino", "FINAL", 75, False),
)

# (desde, hacia, permiso, requiere_motivo)
_TRANSICIONES = (
    ("PRE_ALERT", "BOOKING_ASSIGNED", "shipments.transition.forward", False),
    ("BOOKING_ASSIGNED", "PRE_ALERT", "shipments.transition.backward", True),
    ("BOOKING_ASSIGNED", "IN_TRANSIT", "shipments.transition.forward", False),
    ("IN_TRANSIT", "BOOKING_ASSIGNED", "shipments.transition.backward", True),
    ("IN_TRANSIT", "TRANSSHIPMENT", "shipments.transition.forward", False),
    ("TRANSSHIPMENT", "IN_TRANSIT", "shipments.transition.backward", True),
    ("TRANSSHIPMENT", "AT_DESTINATION", "shipments.transition.forward", False),
    ("AT_DESTINATION", "TRANSSHIPMENT", "shipments.transition.backward", True),
    ("AT_DESTINATION", "DELIVERED", "shipments.transition.forward", False),
    ("DELIVERED", "AT_DESTINATION", "shipments.transition.revert_delivered", True),
)


def upgrade() -> None:
    for code, label, categoria, orden, terminal in _ESTADOS:
        op.execute(
            sa.text("""
                INSERT INTO shipment_statuses
                    (code, label, category, sort_order, is_terminal, is_active)
                VALUES (:code, :label, :categoria, :orden, :terminal, true)
                ON CONFLICT (code) DO UPDATE
                    SET label = EXCLUDED.label, category = EXCLUDED.category,
                        sort_order = EXCLUDED.sort_order, is_active = true
            """).bindparams(
                code=code, label=label, categoria=categoria, orden=orden, terminal=terminal
            )
        )

    for desde, hacia, permiso, motivo in _TRANSICIONES:
        op.execute(
            sa.text("""
                INSERT INTO shipment_status_transitions
                    (from_status_code, to_status_code, required_permission_id,
                     requires_reason, is_active)
                SELECT :desde, :hacia, id, :motivo, true
                FROM permissions WHERE code = :permiso
                ON CONFLICT (from_status_code, to_status_code) DO UPDATE
                    SET required_permission_id = EXCLUDED.required_permission_id,
                        requires_reason = EXCLUDED.requires_reason,
                        is_active = true
            """).bindparams(desde=desde, hacia=hacia, permiso=permiso, motivo=motivo)
        )

    # Cambio de estado libre: Admin y Super admin pueden saltar el flujo.
    op.execute("""
        INSERT INTO permissions (code, resource, action, description)
        VALUES (
            'shipments.status.set_any', 'shipments', 'status.set_any',
            'Pasar una carga a cualquier estado, sin seguir el flujo'
        )
        ON CONFLICT (code) DO UPDATE SET description = EXCLUDED.description
    """)
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id
        FROM roles r CROSS JOIN permissions p
        WHERE r.code IN ('ADMIN', 'SUPER_ADMIN') AND p.code = 'shipments.status.set_any'
        ON CONFLICT (role_id, permission_id) DO NOTHING
    """)
    op.execute("""
        UPDATE users u
        SET authz_version = authz_version + 1, updated_at = now()
        WHERE EXISTS (
            SELECT 1 FROM user_role_assignments ura
            JOIN roles r ON r.id = ura.role_id
            WHERE ura.user_id = u.id AND r.code IN ('ADMIN', 'SUPER_ADMIN')
        )
    """)

    op.add_column("shipments", sa.Column("load_type", sa.String(length=3), nullable=True))
    op.create_check_constraint(
        "tipo_de_carga_valido", "shipments", "load_type IS NULL OR load_type IN ('FCL', 'LCL')"
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_shipments_tipo_de_carga_valido"), "shipments", type_="check")
    op.drop_column("shipments", "load_type")
    op.execute("""
        DELETE FROM role_permissions
        WHERE permission_id = (SELECT id FROM permissions WHERE code = 'shipments.status.set_any')
    """)
    op.execute("DELETE FROM permissions WHERE code = 'shipments.status.set_any'")
    for desde, hacia, _permiso, _motivo in _TRANSICIONES:
        op.execute(
            sa.text(
                "DELETE FROM shipment_status_transitions "
                "WHERE from_status_code = :desde AND to_status_code = :hacia"
            ).bindparams(desde=desde, hacia=hacia)
        )
    # Los estados quedan si alguna carga los usa: borrarlos rompería la FK.
    for code, *_ in _ESTADOS:
        op.execute(
            sa.text("""
                DELETE FROM shipment_statuses s WHERE code = :code
                AND NOT EXISTS (SELECT 1 FROM shipments WHERE current_status_code = s.code)
            """).bindparams(code=code)
        )

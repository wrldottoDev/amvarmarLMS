"""reportes de tránsito, BL, AMAR y permisos de administrador

Revision ID: c7a91e2d4f60
Revises: b9d4e6a1c207
Create Date: 2026-09-30 15:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c7a91e2d4f60"
down_revision: str | Sequence[str] | None = "b9d4e6a1c207"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_shipment_references_reference_type_valido",
        "shipment_references",
        type_="check",
    )
    op.create_check_constraint(
        "reference_type_valido",
        "shipment_references",
        "reference_type IN ('INVOICE', 'WR', 'PO', 'TRACKING', 'CONTAINER', 'BL', 'AMAR', 'OTHER')",
    )

    # Ya no existe un atajo de despacho para cargas de otros orígenes.
    op.execute("""
        DELETE FROM shipment_status_transitions
        WHERE (from_status_code, to_status_code) IN (
            ('IN_TRANSIT', 'DISPATCH_REQUESTED'),
            ('DISPATCH_REQUESTED', 'IN_TRANSIT')
        )
    """)

    op.execute("""
        INSERT INTO permissions (code, resource, action, description)
        VALUES (
            'shipments.delete', 'shipments', 'delete',
            'Eliminar (ocultar) cargas con trazabilidad y recuperación'
        )
        ON CONFLICT (code) DO UPDATE SET description = EXCLUDED.description
    """)
    # ADMIN y SUPER_ADMIN comparten toda la matriz. Esto también incorpora el
    # permiso nuevo y evita 401 por diferencias históricas entre ambos roles.
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id
        FROM roles r CROSS JOIN permissions p
        WHERE r.code IN ('ADMIN', 'SUPER_ADMIN')
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


def downgrade() -> None:
    op.execute("""
        DELETE FROM role_permissions
        WHERE permission_id = (SELECT id FROM permissions WHERE code = 'shipments.delete')
    """)
    op.execute("DELETE FROM permissions WHERE code = 'shipments.delete'")
    # AMAR no existía en el esquema anterior; conservar el valor como una
    # referencia genérica permite bajar sin perder datos.
    op.execute("""
        DELETE FROM shipment_references amar
        USING shipment_references otra
        WHERE amar.reference_type = 'AMAR'
          AND otra.reference_type = 'OTHER'
          AND amar.shipment_id = otra.shipment_id
          AND amar.value = otra.value
    """)
    op.execute("UPDATE shipment_references SET reference_type = 'OTHER' WHERE reference_type = 'AMAR'")
    op.drop_constraint(
        "ck_shipment_references_reference_type_valido",
        "shipment_references",
        type_="check",
    )
    op.create_check_constraint(
        "reference_type_valido",
        "shipment_references",
        "reference_type IN ('INVOICE', 'WR', 'PO', 'TRACKING', 'CONTAINER', 'BL', 'OTHER')",
    )

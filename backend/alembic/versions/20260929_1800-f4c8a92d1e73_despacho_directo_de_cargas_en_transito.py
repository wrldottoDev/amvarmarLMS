"""despacho directo de cargas en transito

Revision ID: f4c8a92d1e73
Revises: e7b1f3c9a852
Create Date: 2026-09-29 18:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f4c8a92d1e73"
down_revision: str | Sequence[str] | None = "e7b1f3c9a852"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Otros orígenes no pasan por la bodega de Miami. El servicio aplica la
    # política que limita este salto a cargas sin una bodega que emita WR.
    op.execute("""
        INSERT INTO shipment_status_transitions
            (from_status_code, to_status_code, required_permission_id,
             requires_reason, is_active)
        SELECT 'IN_TRANSIT', 'DISPATCH_REQUESTED', id, false, true
        FROM permissions WHERE code = 'shipments.transition.forward'
        ON CONFLICT (from_status_code, to_status_code) DO UPDATE
            SET required_permission_id = EXCLUDED.required_permission_id,
                requires_reason = false,
                is_active = true
    """)
    op.execute("""
        INSERT INTO shipment_status_transitions
            (from_status_code, to_status_code, required_permission_id,
             requires_reason, is_active)
        SELECT 'DISPATCH_REQUESTED', 'IN_TRANSIT', id, true, true
        FROM permissions WHERE code = 'shipments.transition.backward'
        ON CONFLICT (from_status_code, to_status_code) DO UPDATE
            SET required_permission_id = EXCLUDED.required_permission_id,
                requires_reason = true,
                is_active = true
    """)

    # Las cargas de tránsito que ya existían no pasaron por RECEIVED/STORED,
    # por lo que todavía no tenían abierta la factura obligatoria.
    op.execute("""
        INSERT INTO shipment_requirements
            (shipment_id, requirement_type, document_type_id, title, description,
             required_from, status, blocks_dispatch, created_by)
        SELECT s.id, 'DOCUMENT', dt.id, dt.label, dt.description,
               CASE WHEN dt.provided_by = 'STAFF' THEN 'STAFF' ELSE 'CLIENT' END,
               CASE WHEN EXISTS (
                   SELECT 1 FROM shipment_documents sd
                   JOIN documents d ON d.id = sd.document_id
                   WHERE sd.shipment_id = s.id
                     AND sd.document_type_id = dt.id
                     AND d.deleted_at IS NULL
                     AND d.upload_status = 'READY'
               ) THEN 'UPLOADED' ELSE 'PENDING' END,
               true, s.created_by
        FROM shipments s
        LEFT JOIN facilities f ON f.id = s.origin_facility_id
        JOIN document_types dt ON dt.is_active
                              AND dt.context = 'SHIPMENT'
                              AND dt.required_before_status IS NOT NULL
        WHERE s.current_status_code = 'IN_TRANSIT'
          AND NOT COALESCE(f.uses_warehouse_receipt, false)
          AND (
              dt.code IN ('COMMERCIAL_INVOICE', 'PACKING_LIST', 'PROOF_OF_DELIVERY')
              OR (dt.code = 'SPECIAL_PERMIT' AND s.permit_review_required)
          )
          AND NOT EXISTS (
              SELECT 1 FROM shipment_requirements existente
              WHERE existente.shipment_id = s.id
                AND existente.document_type_id = dt.id
          )
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM shipment_status_transitions
        WHERE (from_status_code, to_status_code) IN (
            ('IN_TRANSIT', 'DISPATCH_REQUESTED'),
            ('DISPATCH_REQUESTED', 'IN_TRANSIT')
        )
    """)

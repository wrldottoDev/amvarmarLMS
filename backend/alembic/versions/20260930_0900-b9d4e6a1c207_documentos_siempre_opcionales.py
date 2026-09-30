"""documentos siempre opcionales

Revision ID: b9d4e6a1c207
Revises: f4c8a92d1e73
Create Date: 2026-09-30 09:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b9d4e6a1c207"
down_revision: str | Sequence[str] | None = "f4c8a92d1e73"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Conservamos los requisitos y su estado para poder pedir y revisar los
    # archivos, pero ninguno de ellos puede impedir una operación logística.
    op.execute("""
        UPDATE shipment_requirements
        SET blocks_dispatch = false
        WHERE requirement_type = 'DOCUMENT'
    """)
    op.execute("""
        UPDATE document_types
        SET description = CASE code
            WHEN 'COMMERCIAL_INVOICE' THEN
                'Recomendada para la carga. Si el proveedor la entrega directo a AMVARMAR, '
                'Operaciones la carga y deja de ser una acción pendiente del cliente.'
            WHEN 'SLI' THEN
                'Recomendada para cargas originadas en una bodega que la solicita '
                '(aplicabilidad automática, ligada a ADR-0005).'
            WHEN 'SPECIAL_PERMIT' THEN
                'Recomendado si Operaciones determina que la mercancía requiere inspección '
                'o autorización. No impide que la carga avance.'
            WHEN 'PROOF_OF_DELIVERY' THEN
                'Documento firmado, fotografía, comprobante del transportista o confirmación '
                'electrónica. Recomendada para respaldar la entrega.'
            ELSE description
        END
        WHERE code IN ('COMMERCIAL_INVOICE', 'SLI', 'SPECIAL_PERMIT', 'PROOF_OF_DELIVERY')
    """)
    # Los requisitos automáticos copiaron la descripción del catálogo al
    # crearse. Se sincroniza su texto para no seguir diciendo "obligatorio".
    op.execute("""
        UPDATE shipment_requirements r
        SET description = dt.description
        FROM document_types dt
        WHERE r.document_type_id = dt.id
          AND r.requirement_type = 'DOCUMENT'
          AND r.title = dt.label
    """)
    op.create_check_constraint(
        "documentos_no_bloquean",
        "shipment_requirements",
        "requirement_type <> 'DOCUMENT' OR blocks_dispatch = false",
    )


def downgrade() -> None:
    op.drop_constraint(
        "documentos_no_bloquean",
        "shipment_requirements",
        type_="check",
    )
    op.execute("""
        UPDATE shipment_requirements r
        SET blocks_dispatch = true
        FROM document_types dt
        WHERE r.document_type_id = dt.id
          AND r.requirement_type = 'DOCUMENT'
          AND dt.required_before_status IS NOT NULL
    """)

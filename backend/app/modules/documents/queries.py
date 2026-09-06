"""Consultas de lectura reutilizables sobre `documents` (ADR-0012, Fase 4).

Mismo patrón que `shipments/queries.py`: centraliza acá lo que un módulo
externo (el copiloto) necesita leer, para no repetir el JOIN a mano ni meter
SQL directo en un ejecutor.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def factura_de_carga(session: AsyncSession, document_id: UUID) -> Any | None:
    """El documento y la carga a la que pertenece.

    Todo documento se sube ya atado a una carga o a un despacho
    (`documents.service.preparar_subida` exige `shipment_id` desde el
    primer tiempo) — así que `None` significa que no existe, o que está
    atado a un despacho en vez de a una carga."""
    return (
        await session.execute(
            text("""
                SELECT d.id, d.company_id, d.storage_key, d.media_type, d.upload_status,
                       s.id AS shipment_id, s.shipment_number, s.row_version
                FROM documents d
                JOIN shipment_documents sd ON sd.document_id = d.id
                JOIN shipments s ON s.id = sd.shipment_id
                WHERE d.id = :id AND d.deleted_at IS NULL AND s.deleted_at IS NULL
            """),
            {"id": document_id},
        )
    ).one_or_none()

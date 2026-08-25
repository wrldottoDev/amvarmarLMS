"""Siembra el catálogo de tipos de documento (ADR-0003).

Idempotente. Uso:
    python -m scripts.seed_document_types
"""

import asyncio
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_engine, get_sessionmaker
from app.modules.documents.catalog import TIPOS_DOCUMENTO


async def sembrar(session: AsyncSession) -> dict[str, int]:
    for code, definicion in TIPOS_DOCUMENTO.items():
        await session.execute(
            text("""
                INSERT INTO document_types
                    (code, label, description, provided_by, allowed_formats,
                     required_before_status, is_active)
                VALUES (:code, :label, :descripcion, :provided_by, :formatos,
                        :required_before, true)
                ON CONFLICT (code) DO UPDATE
                    SET label = EXCLUDED.label,
                        description = EXCLUDED.description,
                        provided_by = EXCLUDED.provided_by,
                        allowed_formats = EXCLUDED.allowed_formats,
                        required_before_status = EXCLUDED.required_before_status,
                        is_active = true,
                        updated_at = now()
            """),
            {
                "code": str(code),
                "label": definicion.label,
                "descripcion": definicion.description,
                "provided_by": str(definicion.provided_by),
                "formatos": definicion.allowed_formats,
                "required_before": (
                    str(definicion.required_before_status)
                    if definicion.required_before_status
                    else None
                ),
            },
        )

    await session.commit()

    return {
        "document_types": (
            await session.execute(text("SELECT count(*) FROM document_types"))
        ).scalar_one()
    }


async def main() -> None:
    async with get_sessionmaker()() as session:
        conteos = await sembrar(session)

    await get_engine().dispose()

    print("Seed de tipos de documento completado:")
    for tabla, total in conteos.items():
        print(f"  {tabla}: {total}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(1)

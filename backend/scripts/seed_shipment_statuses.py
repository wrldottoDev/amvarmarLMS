"""Siembra el catálogo de estados y transiciones (ADR-0001).

Idempotente, igual que `seed_rbac`. Depende de que los permisos ya estén
sembrados: cada transición apunta al permiso que exige.

Uso:
    python -m scripts.seed_rbac && python -m scripts.seed_shipment_statuses
"""

import asyncio
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_engine, get_sessionmaker
from app.modules.shipments.catalog import ESTADOS, TRANSICIONES


class PermisoFaltante(Exception):
    """Una transición apunta a un permiso que no existe en la base."""


async def sembrar_estados(session: AsyncSession) -> None:
    for code, definicion in ESTADOS.items():
        await session.execute(
            text("""
                INSERT INTO shipment_statuses
                    (code, label, category, sort_order, is_terminal, is_active)
                VALUES (:code, :label, :category, :sort_order, :is_terminal, true)
                ON CONFLICT (code) DO UPDATE
                    SET label = EXCLUDED.label,
                        category = EXCLUDED.category,
                        sort_order = EXCLUDED.sort_order,
                        is_terminal = EXCLUDED.is_terminal,
                        is_active = true
            """),
            {
                "code": str(code),
                "label": definicion.label,
                "category": str(definicion.category),
                "sort_order": definicion.sort_order,
                "is_terminal": definicion.is_terminal,
            },
        )


async def sembrar_transiciones(session: AsyncSession) -> None:
    for transicion in TRANSICIONES:
        resultado = await session.execute(
            text("""
                INSERT INTO shipment_status_transitions
                    (from_status_code, to_status_code, required_permission_id,
                     requires_reason, is_active)
                SELECT :desde, :hacia, p.id, :requiere_motivo, true
                FROM permissions p
                WHERE p.code = :permiso
                ON CONFLICT (from_status_code, to_status_code) DO UPDATE
                    SET required_permission_id = EXCLUDED.required_permission_id,
                        requires_reason = EXCLUDED.requires_reason,
                        is_active = true
                RETURNING from_status_code
            """),
            {
                "desde": str(transicion.desde),
                "hacia": str(transicion.hacia),
                "permiso": transicion.permiso,
                "requiere_motivo": transicion.requiere_motivo,
            },
        )

        if resultado.one_or_none() is None:
            # El INSERT ... SELECT no inserta nada si el permiso no existe, y
            # fallaría en silencio dejando la transición sin declarar.
            raise PermisoFaltante(
                f"El permiso '{transicion.permiso}' no existe. "
                f"Corra `python -m scripts.seed_rbac` primero."
            )

    # Retirar transiciones que el catálogo ya no declara. Sin esto, quitar una
    # transición de ADR-0001 no tendría efecto sobre una base ya sembrada.
    # Dos arrays paralelos en vez de un array de tuplas: asyncpg no soporta
    # el segundo, y un `NOT IN` con pares hay que armarlo así.
    await session.execute(
        text("""
            DELETE FROM shipment_status_transitions t
            WHERE NOT EXISTS (
                SELECT 1
                FROM unnest(CAST(:desde AS varchar[]), CAST(:hacia AS varchar[]))
                     AS vigente(desde, hacia)
                WHERE vigente.desde = t.from_status_code
                  AND vigente.hacia = t.to_status_code
            )
        """),
        {
            "desde": [str(t.desde) for t in TRANSICIONES],
            "hacia": [str(t.hacia) for t in TRANSICIONES],
        },
    )


async def sembrar(session: AsyncSession) -> dict[str, int]:
    await sembrar_estados(session)
    await sembrar_transiciones(session)
    await session.commit()

    return {
        "shipment_statuses": (
            await session.execute(text("SELECT count(*) FROM shipment_statuses"))
        ).scalar_one(),
        "shipment_status_transitions": (
            await session.execute(text("SELECT count(*) FROM shipment_status_transitions"))
        ).scalar_one(),
    }


async def main() -> None:
    async with get_sessionmaker()() as session:
        conteos = await sembrar(session)

    await get_engine().dispose()

    print("Seed de estados completado:")
    for tabla, total in conteos.items():
        print(f"  {tabla}: {total}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(1)

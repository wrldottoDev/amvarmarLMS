"""Reglas de dominio de cargas.

Aquí viven las validaciones que dependen de datos relacionados y por eso no
pueden ser un CHECK de base: un CHECK no puede consultar otra tabla.
"""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflicto, ReglaDeNegocioViolada
from app.modules.shipments.models import ReferenceType


class WarehouseReceiptNoAplica(ReglaDeNegocioViolada):
    code = "WR_NO_APLICA_A_ESTE_ORIGEN"


class WarehouseReceiptFaltante(ReglaDeNegocioViolada):
    code = "SHIPMENT_MISSING_WR"


class WarehouseReceiptRepetido(Conflicto):
    code = "SHIPMENT_WR_REPETIDO"


async def bodega_usa_warehouse_receipt(session: AsyncSession, shipment_id: UUID) -> bool:
    """¿La bodega de origen de esta carga emite WR?

    ADR-0005: la regla NO compara ciudad ni país. Se apoya en el flag
    `facilities.uses_warehouse_receipt`, así que abrir una bodega nueva que use
    WR es activar ese flag, sin tocar código ni políticas.
    """
    return bool(
        (
            await session.execute(
                text("""
                    SELECT COALESCE(f.uses_warehouse_receipt, false)
                    FROM shipments s
                    LEFT JOIN facilities f ON f.id = s.origin_facility_id
                    WHERE s.id = :shipment_id
                """),
                {"shipment_id": shipment_id},
            )
        ).scalar_one_or_none()
    )


async def validar_referencia_permitida(
    session: AsyncSession,
    *,
    shipment_id: UUID,
    reference_type: str,
) -> None:
    """Rechaza un WR en una carga cuyo origen no lo usa.

    Las demás referencias (factura, PO, tracking, contenedor, BL) no tienen
    restricción de origen.
    """
    if reference_type != ReferenceType.WR:
        return

    if not await bodega_usa_warehouse_receipt(session, shipment_id):
        raise WarehouseReceiptNoAplica(
            "El Warehouse Receipt solo aplica a cargas recibidas en una bodega "
            "que lo emite. Use la factura u otra referencia."
        )


async def validar_wr_libre(session: AsyncSession, *, shipment_id: UUID, valor: str) -> None:
    """Un WR no se repite dentro de la misma bodega.

    La base ya lo impide (`uq_shipment_references_wr_facility`), pero esa
    violación llegaba como un 500 al editar y, al dar de alta, el
    `ON CONFLICT DO NOTHING` la tragaba y la carga quedaba sin WR. Se compara
    normalizado igual que el trigger `preparar_referencia_carga`, así que
    "wr-105921" choca con "WR105921".
    """
    otra = (
        await session.execute(
            text("""
                SELECT s.shipment_number
                FROM shipment_references r
                JOIN shipments s ON s.id = r.shipment_id
                WHERE r.reference_type = 'WR'
                  AND r.shipment_id <> :s
                  AND r.facility_id = (SELECT origin_facility_id FROM shipments WHERE id = :s)
                  AND r.normalized_value
                      = regexp_replace(upper(btrim(:v)), '[^A-Z0-9]+', '', 'g')
                LIMIT 1
            """),
            {"s": shipment_id, "v": valor},
        )
    ).scalar_one_or_none()

    if otra is not None:
        raise WarehouseReceiptRepetido(
            f"El Warehouse Receipt {valor} ya lo tiene la carga {otra} de esta bodega."
        )


async def validar_wr_presente_para_almacenar(session: AsyncSession, shipment_id: UUID) -> None:
    """Dirección inversa de la regla (ADR-0005).

    Antes solo se prohibía el WR fuera de Miami; ahora Miami además lo **exige**
    antes de considerar la carga almacenada. Se invoca desde la transición
    `RECEIVED → STORED` (Paso 2.4).
    """
    if not await bodega_usa_warehouse_receipt(session, shipment_id):
        return

    tiene_wr = (
        await session.execute(
            text("""
                SELECT 1 FROM shipment_references
                WHERE shipment_id = :shipment_id AND reference_type = 'WR'
                LIMIT 1
            """),
            {"shipment_id": shipment_id},
        )
    ).scalar_one_or_none()

    if tiene_wr is None:
        raise WarehouseReceiptFaltante(
            "La carga se recibió en una bodega que emite Warehouse Receipt: "
            "registre el WR antes de marcarla como almacenada."
        )


class IdentificadorFaltante(ReglaDeNegocioViolada):
    code = "SHIPMENT_MISSING_IDENTIFIER"


async def validar_identificador_comercial(
    session: AsyncSession,
    *,
    shipment_id: UUID,
    origin_facility_id: UUID | None,
    invoice: str | None,
    wr: str | None,
) -> None:
    """Toda carga tiene que poder buscarse por su identificador de negocio.

    La regla del negocio, confirmada con AMVARMAR: **lo que viene de una bodega
    que emite Warehouse Receipt lleva WR, y es obligatorio desde el alta; todo
    lo demás se identifica por su factura.**

    Sin esto, una carga que no viene de Miami y a la que nadie le puso factura
    solo se puede encontrar por su número interno, que es un dato que el cliente
    no conoce y que no aparece en ningún papel del embarque.

    El flujo de alta es el mismo para las dos: cambia qué campo se exige, no los
    pasos.
    """
    if origin_facility_id is not None:
        usa_wr = (
            await session.execute(
                text(
                    "SELECT COALESCE(uses_warehouse_receipt, false) FROM facilities WHERE id = :f"
                ),
                {"f": origin_facility_id},
            )
        ).scalar_one_or_none()

        if usa_wr:
            # Antes el WR podía faltar al dar de alta y se exigía recién al
            # almacenar. AMVARMAR lo pidió obligatorio desde el alta
            # (2026-11-05).
            if (wr or "").strip():
                return
            raise WarehouseReceiptFaltante(
                "Las cargas de una bodega que emite Warehouse Receipt necesitan su WR."
            )

    if (invoice or "").strip():
        return

    # Puede que la factura ya esté cargada como referencia de una edición previa.
    ya_tiene = (
        await session.execute(
            text("""
                SELECT 1 FROM shipment_references
                WHERE shipment_id = :s AND reference_type = 'INVOICE'
                  AND coalesce(trim(value), '') <> ''
                LIMIT 1
            """),
            {"s": shipment_id},
        )
    ).scalar_one_or_none()

    if ya_tiene is None:
        raise IdentificadorFaltante(
            "Esta carga no viene de una bodega que emita Warehouse Receipt, así que "
            "necesita el número de factura para poder encontrarla."
        )

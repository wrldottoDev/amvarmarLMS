"""Ayudante compartido para el invariante de piezas.

Toda carga activa necesita al menos una pieza: lo exige el servicio y lo vigila
un constraint diferible en la base. Las pruebas que insertan cargas con SQL
directo tienen que respetarlo igual, y repetir el mismo INSERT en once archivos
garantiza que alguno quede sin él la próxima vez que se agregue un helper.
"""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def sembrar_pieza(
    session: AsyncSession,
    shipment_id: UUID,
    *,
    package_type: str = "BOX",
    quantity: int = 1,
) -> None:
    """Una pieza mínima para que la carga cumpla el invariante.

    El valor por defecto es deliberadamente aburrido: una caja. Las pruebas que
    miran el desglose ponen el suyo; las que solo necesitan una carga válida no
    deberían tener que pensar en esto.
    """
    await session.execute(
        text("""
            INSERT INTO shipment_packages (shipment_id, package_type, quantity)
            VALUES (:s, :tipo, :cantidad)
        """),
        {"s": shipment_id, "tipo": package_type, "cantidad": quantity},
    )

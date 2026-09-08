"""Columnas que cada persona ve en cada listado.

El sistema viejo dejaba a cada usuario elegir qué columnas mostrar en el listado
de cargas, y esa preferencia se guardaba. Con nueve columnas posibles no es un
lujo: quien factura mira CFTS y peso, quien rastrea mira tracking y WR, y
obligar a los dos a la misma vista hace que ninguno la tenga cómoda.
"""

import json
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class DefinicionColumna:
    clave: str
    etiqueta: str
    # Si es fija, no se puede quitar: sin ella el listado no se puede usar.
    fija: bool = False


# Las mismas del sistema viejo, más las que el nuevo agrega. El orden es el que
# aparece por defecto.
COLUMNAS_CARGAS: list[DefinicionColumna] = [
    DefinicionColumna("identificador", "WR / Factura", fija=True),
    DefinicionColumna("empresa", "Empresa"),
    # Además del identificador: una carga de Miami tiene WR **y** puede tener
    # factura, y el listado viejo mostraba las dos columnas por separado.
    DefinicionColumna("invoice", "Invoice #"),
    DefinicionColumna("estado", "Estado", fija=True),
    DefinicionColumna("shipper", "Shipper"),
    DefinicionColumna("carrier", "Carrier"),
    DefinicionColumna("foots_cft", "CFTS"),
    DefinicionColumna("tracking", "Tracking"),
    DefinicionColumna("po", "PO"),
    DefinicionColumna("container", "Contenedor"),
    DefinicionColumna("peso", "Peso"),
    DefinicionColumna("bultos", "Bultos"),
    DefinicionColumna("pendientes", "Pendientes"),
    DefinicionColumna("fecha", "Fecha"),
]

VISTAS: dict[str, list[DefinicionColumna]] = {"shipments": COLUMNAS_CARGAS}


def por_defecto(vista: str) -> list[str]:
    """Todas las columnas visibles, como en el sistema viejo."""
    return [c.clave for c in VISTAS.get(vista, [])]


def normalizar(vista: str, elegidas: list[str]) -> list[str]:
    """Deja solo columnas que existen y devuelve siempre las fijas.

    Una preferencia guardada hace un año puede nombrar columnas que ya no
    existen; filtrarlas evita que la pantalla se rompa por un dato viejo. Y las
    fijas se reponen aunque el cliente las mande quitadas: sin identificador ni
    estado, el listado no sirve para nada.
    """
    definiciones = VISTAS.get(vista, [])
    validas = {c.clave for c in definiciones}
    fijas = [c.clave for c in definiciones if c.fija]

    resultado = [c for c in elegidas if c in validas]
    for fija in fijas:
        if fija not in resultado:
            # Se repone en su posición original, no al final.
            posicion = next(i for i, c in enumerate(definiciones) if c.clave == fija)
            resultado.insert(min(posicion, len(resultado)), fija)
    return resultado


async def obtener(session: AsyncSession, *, user_id: UUID, vista: str) -> list[str]:
    guardadas = (
        await session.execute(
            text("SELECT columnas FROM column_preferences WHERE user_id = :u AND vista = :v"),
            {"u": user_id, "v": vista},
        )
    ).scalar_one_or_none()

    if not guardadas:
        return por_defecto(vista)
    return normalizar(vista, list(guardadas))


async def guardar(
    session: AsyncSession, *, user_id: UUID, vista: str, columnas: list[str]
) -> list[str]:
    normalizadas = normalizar(vista, columnas)
    await session.execute(
        text("""
            INSERT INTO column_preferences (user_id, vista, columnas)
            VALUES (:u, :v, CAST(:c AS JSONB))
            ON CONFLICT (user_id, vista)
            DO UPDATE SET columnas = EXCLUDED.columnas, updated_at = now()
        """),
        {"u": user_id, "v": vista, "c": json.dumps(normalizadas)},
    )
    return normalizadas

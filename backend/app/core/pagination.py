"""Paginación por cursor (keyset), no por offset.

Con `OFFSET`, insertar una fila mientras alguien recorre las páginas desplaza
todo hacia abajo: la fila que estaba al final de la página 1 reaparece al
inicio de la página 2. Con cursor se pagina sobre `(created_at, id)`, así que
lo que se inserta aparece arriba y no altera las páginas ya servidas.

El cursor es opaco a propósito: si fuera un número de página, el cliente
intentaría construirlo, y el día que cambie el criterio de orden se rompería.
"""

import base64
import binascii
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.core.errors import ErrorDeAplicacion

LIMITE_POR_DEFECTO = 25
# Tope duro del servidor. Un cliente que pida 10000 recibe 100, no un error:
# recortar es más amable que fallar y el resultado sigue siendo correcto.
LIMITE_MAXIMO = 100


class CursorInvalido(ErrorDeAplicacion):
    code = "CURSOR_INVALIDO"


@dataclass(frozen=True)
class Cursor:
    created_at: datetime
    id: UUID

    def codificar(self) -> str:
        crudo = json.dumps({"c": self.created_at.isoformat(), "i": str(self.id)})
        return base64.urlsafe_b64encode(crudo.encode()).decode().rstrip("=")

    @classmethod
    def decodificar(cls, valor: str) -> "Cursor":
        try:
            # El padding se quita al codificar para que el cursor no lleve `=`
            # y viaje limpio en una query string.
            relleno = "=" * (-len(valor) % 4)
            datos = json.loads(base64.urlsafe_b64decode(valor + relleno))
            return cls(created_at=datetime.fromisoformat(datos["c"]), id=UUID(datos["i"]))
        except (
            binascii.Error,
            ValueError,
            KeyError,
            TypeError,
            json.JSONDecodeError,
        ) as error:
            raise CursorInvalido("El cursor de paginación no es válido.") from error


@dataclass(frozen=True)
class Pagina[T]:
    items: list[T]
    next_cursor: str | None
    has_more: bool


def normalizar_limite(limite: int | None) -> int:
    if limite is None:
        return LIMITE_POR_DEFECTO
    return max(1, min(limite, LIMITE_MAXIMO))


def armar_pagina[T](
    filas: list[T],
    *,
    limite: int,
    cursor_de: Callable[[T], Cursor],
) -> Pagina[T]:
    """Recorta a `limite` y calcula el cursor siguiente.

    La consulta pide `limite + 1` filas: si vuelve la de más, hay página
    siguiente. Es una comparación, no un `COUNT(*)` sobre toda la tabla.
    """
    hay_mas = len(filas) > limite
    visibles = filas[:limite]

    siguiente = None
    if hay_mas and visibles:
        siguiente = cursor_de(visibles[-1]).codificar()

    return Pagina(items=visibles, next_cursor=siguiente, has_more=hay_mas)

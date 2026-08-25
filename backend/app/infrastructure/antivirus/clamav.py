"""Cliente de ClamAV sobre el protocolo INSTREAM.

Se implementa directamente en vez de usar el paquete `clamd` porque ese es
síncrono: bloquearía el event loop durante todo el escaneo, que en un archivo
de 250 MB no es despreciable.

El protocolo INSTREAM es simple: se envía `zINSTREAM\\0`, luego el archivo en
trozos precedidos por su longitud en 4 bytes big-endian, y un trozo de longitud
cero para cerrar. ClamAV responde con una línea.
"""

import asyncio
import contextlib
import struct
from dataclasses import dataclass
from enum import StrEnum

from app.core.config import get_settings


class ResultadoEscaneo(StrEnum):
    LIMPIO = "LIMPIO"
    INFECTADO = "INFECTADO"


class EscanerNoDisponible(Exception):
    """No se pudo contactar a ClamAV, o respondió algo que no se entiende.

    NO significa "limpio". El llamador debe dejar el documento pendiente: un
    escáner caído no puede autorizar nada (fail closed).
    """


@dataclass(frozen=True)
class Veredicto:
    resultado: ResultadoEscaneo
    # Nombre de la firma que coincidió, ej. `Eicar-Signature`. Se guarda para
    # poder investigar un falso positivo sin volver a escanear.
    amenaza: str | None = None


# ClamAV rechaza trozos mayores a su `StreamMaxLength`. 64 KB es holgado y
# mantiene el uso de memoria plano sin importar el tamaño del archivo.
_TAMANO_TROZO = 64 * 1024

# Fin de flujo: un trozo de longitud cero.
_FIN_DE_FLUJO = struct.pack("!L", 0)


async def escanear(contenido: bytes) -> Veredicto:
    """Escanea un archivo en memoria.

    Lanza `EscanerNoDisponible` ante cualquier problema de comunicación. Nunca
    devuelve `LIMPIO` por defecto: si no se pudo verificar, no se sabe.
    """
    settings = get_settings()

    try:
        lector, escritor = await asyncio.wait_for(
            asyncio.open_connection(settings.clamav_host, settings.clamav_port),
            timeout=settings.clamav_timeout_seconds,
        )
    except (OSError, TimeoutError) as error:
        raise EscanerNoDisponible("No se pudo conectar con el antivirus.") from error

    try:
        return await asyncio.wait_for(
            _dialogar(lector, escritor, contenido),
            timeout=settings.clamav_timeout_seconds,
        )
    except TimeoutError as error:
        raise EscanerNoDisponible("El antivirus no respondió a tiempo.") from error
    except (OSError, ValueError) as error:
        raise EscanerNoDisponible("Fallo al comunicarse con el antivirus.") from error
    finally:
        escritor.close()
        # `wait_closed` puede fallar si el otro extremo ya cortó; no importa,
        # el veredicto ya se obtuvo.
        with contextlib.suppress(OSError):
            await escritor.wait_closed()


async def _dialogar(
    lector: asyncio.StreamReader, escritor: asyncio.StreamWriter, contenido: bytes
) -> Veredicto:
    escritor.write(b"zINSTREAM\0")

    for inicio in range(0, len(contenido), _TAMANO_TROZO):
        trozo = contenido[inicio : inicio + _TAMANO_TROZO]
        escritor.write(struct.pack("!L", len(trozo)) + trozo)
        # Drenar entre trozos evita acumular todo el archivo en el buffer de
        # salida cuando el escáner va más lento que la escritura.
        await escritor.drain()

    escritor.write(_FIN_DE_FLUJO)
    await escritor.drain()

    respuesta = (await lector.read(4096)).decode("utf-8", errors="replace").strip("\0 \n")

    return _interpretar(respuesta)


def _interpretar(respuesta: str) -> Veredicto:
    """Traduce la respuesta de ClamAV.

    Formatos posibles:
      `stream: OK`
      `stream: Eicar-Signature FOUND`
      `stream: <razón> ERROR`
    """
    if respuesta.endswith("OK"):
        return Veredicto(resultado=ResultadoEscaneo.LIMPIO)

    if respuesta.endswith("FOUND"):
        # `stream: Eicar-Signature FOUND` -> `Eicar-Signature`
        cuerpo = respuesta.rpartition(":")[2].strip()
        amenaza = cuerpo.removesuffix("FOUND").strip() or "desconocida"
        return Veredicto(resultado=ResultadoEscaneo.INFECTADO, amenaza=amenaza)

    # `ERROR` o cualquier cosa inesperada: no se pudo verificar. Tratarlo como
    # limpio sería exactamente el fail open que hay que evitar.
    raise EscanerNoDisponible(f"Respuesta inesperada del antivirus: {respuesta!r}")


async def esta_disponible() -> bool:
    """PING/PONG. Para el health check, no para decidir sobre un documento."""
    settings = get_settings()
    try:
        lector, escritor = await asyncio.wait_for(
            asyncio.open_connection(settings.clamav_host, settings.clamav_port),
            timeout=settings.clamav_timeout_seconds,
        )
    except (OSError, TimeoutError):
        return False

    try:
        escritor.write(b"zPING\0")
        await escritor.drain()
        respuesta = (await asyncio.wait_for(lector.read(64), timeout=5)).strip(b"\0 \n")
        return respuesta == b"PONG"
    except (OSError, TimeoutError):
        return False
    finally:
        escritor.close()
        with contextlib.suppress(OSError):
            await escritor.wait_closed()

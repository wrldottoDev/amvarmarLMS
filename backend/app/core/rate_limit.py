"""Rate limiting con ventana fija en Redis.

Ventana fija y no deslizante: es una operación atómica de dos comandos
(`INCR` + `EXPIRE`) y no requiere guardar la marca de cada intento. A cambio,
permite hasta el doble del límite justo en el borde entre ventanas — aceptable
para frenar fuerza bruta, que necesita órdenes de magnitud más intentos.
"""

from dataclasses import dataclass

from redis.asyncio import Redis

from app.core.errors import DemasiadasSolicitudes


@dataclass(frozen=True)
class Limite:
    intentos: int
    ventana_segundos: int


# Login por cuenta: frena el ataque dirigido contra un usuario concreto.
LIMITE_LOGIN_POR_CUENTA = Limite(intentos=5, ventana_segundos=300)

# Login por IP, más holgado: varias personas legítimas pueden compartir una
# salida NAT (una oficina entera). Demasiado estricto y se bloquean entre sí.
LIMITE_LOGIN_POR_IP = Limite(intentos=30, ventana_segundos=300)

# El refresh es una operación normal y frecuente del cliente; el límite solo
# frena un bucle descontrolado.
LIMITE_REFRESH_POR_IP = Limite(intentos=60, ventana_segundos=60)

# Recuperación de contraseña: cada intento manda un correo.
LIMITE_PASSWORD_FORGOT = Limite(intentos=3, ventana_segundos=900)


async def consumir(
    redis: Redis,
    *,
    clave: str,
    limite: Limite,
) -> None:
    """Registra un intento. Lanza `DemasiadasSolicitudes` si se pasó del límite."""
    clave_redis = f"rl:{clave}"

    actual = await redis.incr(clave_redis)
    if actual == 1:
        # Primer intento de la ventana: recién aquí se fija el vencimiento.
        await redis.expire(clave_redis, limite.ventana_segundos)

    if actual > limite.intentos:
        ttl = await redis.ttl(clave_redis)
        raise DemasiadasSolicitudes(
            "Demasiados intentos. Espere antes de volver a intentar.",
            # TTL negativo significa clave sin vencimiento (no debería pasar):
            # se responde con la ventana completa en vez de un número absurdo.
            retry_after_segundos=ttl if ttl > 0 else limite.ventana_segundos,
        )


async def limpiar(redis: Redis, *, clave: str) -> None:
    """Borra el contador. Se llama tras un login exitoso: quien acertó la
    contraseña no debe arrastrar los fallos anteriores."""
    await redis.delete(f"rl:{clave}")


async def restantes(redis: Redis, *, clave: str, limite: Limite) -> int:
    """Cuántos intentos quedan en la ventana actual, sin consumir uno.

    Usado por `GET /copilot/capabilities` (ADR-0012): mostrar la cuota
    restante no puede, en sí mismo, gastar cuota.
    """
    actual = await redis.get(f"rl:{clave}")
    usados = int(actual) if actual is not None else 0
    return max(0, limite.intentos - usados)


async def sumar(redis: Redis, *, clave: str, cantidad: int, ventana_segundos: int) -> None:
    """Suma `cantidad` al contador sin lanzar si se pasa del límite.

    Para registrar gasto que YA ocurrió — los tokens de un turno del
    copiloto (ADR-0012) que ya se le respondió a la persona — no para
    bloquear un intento antes de que pase. El bloqueo va aparte, con
    `restantes` antes de empezar el turno.
    """
    if cantidad <= 0:
        return
    clave_redis = f"rl:{clave}"
    nuevo = await redis.incrby(clave_redis, cantidad)
    if nuevo == cantidad:
        # Primera suma de la ventana: recién aquí se fija el vencimiento.
        await redis.expire(clave_redis, ventana_segundos)

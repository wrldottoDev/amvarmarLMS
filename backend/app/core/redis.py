from collections.abc import AsyncGenerator
from functools import lru_cache

from redis.asyncio import ConnectionPool, Redis

from app.core.config import get_settings


@lru_cache
def get_pool() -> ConnectionPool:
    return ConnectionPool.from_url(get_settings().redis_url, decode_responses=True)


async def get_redis() -> AsyncGenerator[Redis]:
    """Dependencia de FastAPI: un cliente Redis por request, sobre un pool compartido."""
    client = Redis(connection_pool=get_pool())
    try:
        yield client
    finally:
        # Devuelve la conexión al pool; no cierra el pool en sí.
        await client.aclose()

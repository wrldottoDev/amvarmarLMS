"""Fixtures compartidas.

Los tests de integración corren contra un PostgreSQL real levantado con
testcontainers, no contra SQLite: el proyecto depende de CITEXT, pgcrypto,
UUID nativo y TIMESTAMPTZ, que SQLite no tiene.
"""

import os
from collections.abc import AsyncGenerator, Generator
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

if TYPE_CHECKING:
    from httpx import AsyncClient
    from testcontainers.core.container import DockerContainer


@pytest.fixture(scope="session")
def postgres_container() -> Generator[PostgresContainer]:
    with PostgresContainer("postgres:16-alpine", driver="asyncpg") as container:
        yield container


@pytest.fixture(scope="session")
def redis_container() -> Generator[RedisContainer]:
    with RedisContainer("redis:7-alpine") as container:
        yield container


@pytest.fixture
async def redis(redis_container: RedisContainer) -> AsyncGenerator[Redis]:
    """Cliente Redis con la base vaciada antes de cada test.

    Sin el flush, una entrada cacheada de un test anterior haría pasar (o
    fallar) al siguiente por razones ajenas a lo que prueba.
    """
    url = f"redis://{redis_container.get_container_host_ip()}:{redis_container.get_exposed_port(6379)}/0"
    client = Redis.from_url(url, decode_responses=True)
    await client.flushdb()
    yield client
    await client.aclose()


@pytest.fixture(scope="session")
def database_url(postgres_container: PostgresContainer) -> str:
    return postgres_container.get_connection_url()


@pytest.fixture(scope="session")
def migrated_database(database_url: str) -> str:
    """Aplica todas las migraciones sobre el contenedor recién levantado.

    Correr Alembic (y no Base.metadata.create_all) es deliberado: verifica que
    las migraciones realmente construyen el esquema, no solo que los modelos
    son válidos.
    """
    config = Config("alembic.ini")
    config.set_main_option("script_location", "alembic")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    return database_url


@pytest.fixture
async def session(migrated_database: str) -> AsyncGenerator[AsyncSession]:
    """Sesión aislada: todo lo que escribe el test se revierte al terminar.

    La sesión se ata a una transacción externa que nunca se confirma. Así cada
    test parte de la misma base y no depende del orden de ejecución — necesario
    para poder correr la suite en paralelo con pytest-xdist.
    """
    engine = create_async_engine(migrated_database)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        factory = async_sessionmaker(bind=connection, expire_on_commit=False)
        async with factory() as session:
            yield session
        await transaction.rollback()
    await engine.dispose()


@pytest.fixture
async def cliente(
    migrated_database: str, redis_container: RedisContainer
) -> AsyncGenerator["AsyncClient"]:
    """Cliente HTTP contra la app real, apuntando a los contenedores de prueba.

    Se sobreescriben las dependencias en vez de variables de entorno para no
    contaminar el `Settings` cacheado de otros tests.
    """
    from httpx import ASGITransport, AsyncClient
    from redis.asyncio import Redis

    from app.core.database import get_session
    from app.core.redis import get_redis
    from app.main import app

    engine = create_async_engine(migrated_database)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    url_redis = (
        f"redis://{redis_container.get_container_host_ip()}:"
        f"{redis_container.get_exposed_port(6379)}/0"
    )
    redis_cliente = Redis.from_url(url_redis, decode_responses=True)
    await redis_cliente.flushdb()

    async def _session() -> AsyncGenerator[AsyncSession]:
        async with factory() as s:
            yield s

    async def _redis() -> AsyncGenerator[Redis]:
        yield redis_cliente

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_redis] = _redis

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://pruebas") as http:
        yield http

    app.dependency_overrides.clear()
    await redis_cliente.aclose()
    await engine.dispose()


@pytest.fixture
async def db_directa(migrated_database: str) -> AsyncGenerator[AsyncSession]:
    """Sesión que SÍ confirma, para preparar datos que el cliente HTTP verá.

    La fixture `session` revierte todo al terminar, así que no sirve para
    sembrar datos que otra conexión (la de la app) tiene que leer.
    """
    engine = create_async_engine(migrated_database)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.fixture(scope="session")
def minio_container() -> Generator["DockerContainer"]:
    """MinIO efímero para los tests de storage.

    Se usa `DockerContainer` genérico en vez de `testcontainers.minio` para no
    sumar la dependencia `minio` solo por levantar el contenedor: el proyecto
    habla S3 por boto3, no por el SDK de MinIO.
    """
    import time
    import urllib.error
    import urllib.request

    from testcontainers.core.container import DockerContainer

    contenedor = (
        DockerContainer("minio/minio:latest")
        .with_command("server /data")
        .with_env("MINIO_ROOT_USER", "pruebas")
        .with_env("MINIO_ROOT_PASSWORD", "pruebas-secreto-largo")
        .with_exposed_ports(9000)
    )
    with contenedor:
        # Se espera al endpoint de salud y no a un mensaje de log: el formato
        # de los logs de MinIO cambia entre versiones, el health check no.
        salud = (
            f"http://{contenedor.get_container_host_ip()}:"
            f"{contenedor.get_exposed_port(9000)}/minio/health/live"
        )
        limite = time.monotonic() + 60
        while True:
            try:
                with urllib.request.urlopen(salud, timeout=2):
                    break
            except (urllib.error.URLError, OSError):
                if time.monotonic() > limite:
                    raise TimeoutError("MinIO no respondió al health check.") from None
                time.sleep(0.5)

        yield contenedor


@pytest.fixture
def storage_de_prueba(minio_container: "DockerContainer") -> Generator[str]:
    """Apunta el módulo de storage al MinIO efímero y crea un bucket limpio.

    Se limpian las cachés de `Settings` y del cliente boto3 antes y después:
    ambas son `lru_cache` y arrastrarían la configuración de otro test.
    """
    import uuid as _uuid

    from app.core.config import get_settings
    from app.infrastructure.storage import s3

    bucket = f"pruebas-{_uuid.uuid4().hex[:12]}"
    host = minio_container.get_container_host_ip()
    puerto = minio_container.get_exposed_port(9000)

    anteriores = {
        "S3_ENDPOINT_URL": os.environ.get("S3_ENDPOINT_URL"),
        "S3_ACCESS_KEY": os.environ.get("S3_ACCESS_KEY"),
        "S3_SECRET_KEY": os.environ.get("S3_SECRET_KEY"),
        "S3_BUCKET": os.environ.get("S3_BUCKET"),
    }
    os.environ["S3_ENDPOINT_URL"] = f"http://{host}:{puerto}"
    os.environ["S3_ACCESS_KEY"] = "pruebas"
    os.environ["S3_SECRET_KEY"] = "pruebas-secreto-largo"
    os.environ["S3_BUCKET"] = bucket

    get_settings.cache_clear()
    s3._cliente.cache_clear()

    # Se usa la misma función que en producción para que la configuración del
    # bucket de prueba (privado + cifrado) no pueda divergir de la real.
    s3.asegurar_bucket_privado()

    yield bucket

    for clave, valor in anteriores.items():
        if valor is None:
            os.environ.pop(clave, None)
        else:
            os.environ[clave] = valor
    get_settings.cache_clear()
    s3._cliente.cache_clear()


@pytest.fixture(scope="session")
def mailpit_container() -> Generator["DockerContainer"]:
    """Mailpit efímero: relay SMTP real con API para leer lo recibido.

    Se prueba contra un servidor de verdad y no contra un doble, porque lo que
    puede fallar es justamente el diálogo SMTP: cabeceras mal armadas, multipart
    inválido, timeouts. Un doble que acepta cualquier cosa no encuentra nada.
    """
    import time
    import urllib.error
    import urllib.request

    from testcontainers.core.container import DockerContainer

    contenedor = (
        DockerContainer("axllent/mailpit:latest")
        .with_exposed_ports(1025, 8025)
        .with_env("MP_SMTP_AUTH_ACCEPT_ANY", "1")
        .with_env("MP_SMTP_AUTH_ALLOW_INSECURE", "1")
    )
    with contenedor:
        salud = (
            f"http://{contenedor.get_container_host_ip()}:"
            f"{contenedor.get_exposed_port(8025)}/readyz"
        )
        limite = time.monotonic() + 60
        while True:
            try:
                with urllib.request.urlopen(salud, timeout=2):
                    break
            except (urllib.error.URLError, OSError):
                if time.monotonic() > limite:
                    raise TimeoutError("Mailpit no respondió al health check.") from None
                time.sleep(0.5)

        yield contenedor


@pytest.fixture
def correo_de_prueba(mailpit_container: "DockerContainer") -> Generator[str]:
    """Apunta el envío al Mailpit efímero y deja la bandeja vacía.

    Devuelve la URL base de la API para que las pruebas lean lo recibido.
    """
    import httpx

    from app.core.config import get_settings

    host = mailpit_container.get_container_host_ip()
    smtp = mailpit_container.get_exposed_port(1025)
    api = f"http://{host}:{mailpit_container.get_exposed_port(8025)}"

    anteriores = {
        "SMTP_HOST": os.environ.get("SMTP_HOST"),
        "SMTP_PORT": os.environ.get("SMTP_PORT"),
        "SMTP_USE_TLS": os.environ.get("SMTP_USE_TLS"),
        "FRONTEND_BASE_URL": os.environ.get("FRONTEND_BASE_URL"),
    }
    os.environ["SMTP_HOST"] = host
    os.environ["SMTP_PORT"] = str(smtp)
    os.environ["SMTP_USE_TLS"] = "false"
    os.environ["FRONTEND_BASE_URL"] = "https://app.amvarmar.test"
    get_settings.cache_clear()

    # Bandeja limpia: cada prueba cuenta lo que ella misma generó.
    httpx.delete(f"{api}/api/v1/messages", timeout=5).raise_for_status()

    yield api

    for clave, valor in anteriores.items():
        if valor is None:
            os.environ.pop(clave, None)
        else:
            os.environ[clave] = valor
    get_settings.cache_clear()

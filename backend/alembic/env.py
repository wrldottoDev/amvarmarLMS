import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import get_settings
from app.core.database import Base

# Importar aquí todo módulo de modelos para que Base.metadata los conozca.
# Sin esto, `alembic revision --autogenerate` genera migraciones vacías y
# `alembic check` no detecta modelos sin migración.
from app.modules.audit import legacy as audit_legacy  # noqa: F401
from app.modules.audit import models as audit_models  # noqa: F401
from app.modules.auth import models as auth_models  # noqa: F401
from app.modules.companies import models as companies_models  # noqa: F401
from app.modules.dispatches import models as dispatches_models  # noqa: F401
from app.modules.documents import models as documents_models  # noqa: F401
from app.modules.notifications import models as notifications_models  # noqa: F401
from app.modules.rbac import models as rbac_models  # noqa: F401
from app.modules.shipments import models as shipments_models  # noqa: F401
from app.modules.users import models as users_models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# La URL viene de Settings (.env), nunca de alembic.ini — así no queda una
# credencial escrita en un archivo versionado.
# Si quien invoca ya fijó una URL (los tests apuntan al contenedor efímero de
# testcontainers), se respeta: sobrescribirla haría que las migraciones de los
# tests corran contra la base de desarrollo.
if not config.get_main_option("sqlalchemy.url", None):
    config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Genera SQL sin conectarse a la base."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # compare_type/compare_server_default: sin ambos, `alembic check` no
        # detecta cambios de tipo ni de default, y pasa en verde con modelos
        # que en realidad divergen del esquema.
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

from collections.abc import AsyncGenerator
from datetime import datetime
from functools import lru_cache
from typing import Annotated
from uuid import UUID

from sqlalchemy import MetaData, text
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.config import get_settings

# Convención de nombres de constraints (sección 3.1 del documento de arquitectura).
# Sin esto, Alembic autogenera nombres aleatorios que hacen imposible un downgrade
# limpio: no se puede eliminar por nombre algo cuyo nombre no se conoce.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    # Todo `Mapped[datetime]` del proyecto se convierte en TIMESTAMPTZ, incluidos
    # los nullables. Declararlo aquí y no columna por columna hace imposible que
    # una tabla futura se cree con TIMESTAMP sin zona por descuido (sección 3.1).
    type_annotation_map = {  # noqa: RUF012
        datetime: TIMESTAMP(timezone=True),
    }


# PK UUID generada en la base con gen_random_uuid() (pgcrypto), no en Python:
# el valor existe aunque la fila se inserte por SQL directo o por el migrador
# legacy de Fase 5.
UUIDPk = Annotated[
    UUID,
    mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")),
]


class TimestampMixin:
    """created_at/updated_at manejados por la base, no por la aplicación."""

    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(
        server_default=text("now()"), onupdate=text("now()")
    )


@lru_cache
def get_engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        echo=settings.debug,
    )


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: sin esto, acceder a un atributo después del commit
    # dispara un refresh implícito (I/O lazy), que en async es exactamente el
    # antipatrón que hay que evitar.
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession]:
    """Dependencia de FastAPI: una sesión por request.

    Una AsyncSession es mutable y NO debe compartirse entre tareas concurrentes.
    Cada request abre la suya y la cierra al terminar.
    """
    async with get_sessionmaker()() as session:
        yield session

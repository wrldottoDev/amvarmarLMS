"""Verifica que el esquema base y las convenciones del Paso 1.2 funcionan.

No hay modelos todavía (Paso 1.3 introduce users/companies/memberships), así
que estas pruebas usan una tabla temporal para ejercitar los tipos que toda
tabla del proyecto va a usar.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


async def test_extensiones_instaladas(session: AsyncSession) -> None:
    result = await session.execute(
        text("SELECT extname FROM pg_extension WHERE extname IN ('pgcrypto', 'citext')")
    )
    assert set(result.scalars().all()) == {"pgcrypto", "citext"}


async def test_gen_random_uuid_disponible(session: AsyncSession) -> None:
    """Toda PK del proyecto usa gen_random_uuid() como server_default."""
    result = await session.execute(text("SELECT gen_random_uuid()"))
    generado = result.scalar()
    assert generado is not None

    otro = (await session.execute(text("SELECT gen_random_uuid()"))).scalar()
    assert generado != otro


async def test_citext_es_insensible_a_mayusculas(session: AsyncSession) -> None:
    """users.email será CITEXT UNIQUE (Paso 1.3): la unicidad no debe depender
    de que la aplicación recuerde normalizar a minúsculas."""
    result = await session.execute(
        text("SELECT 'Cliente@AMVARMAR.com'::citext = 'cliente@amvarmar.com'::citext")
    )
    assert result.scalar() is True


async def test_escritura_y_lectura_con_tipos_del_proyecto(session: AsyncSession) -> None:
    """Ciclo completo escribir/leer con UUID PK + TIMESTAMPTZ."""
    await session.execute(
        text("""
            CREATE TEMPORARY TABLE prueba_convenciones (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                email CITEXT NOT NULL UNIQUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
    )
    await session.execute(
        text("INSERT INTO prueba_convenciones (email) VALUES ('Otto@Amvarmar.com')")
    )

    fila = (
        await session.execute(
            text("SELECT id, email, created_at FROM prueba_convenciones WHERE email = :email"),
            {"email": "otto@amvarmar.com"},  # distinta capitalización a propósito
        )
    ).one()

    assert fila.id is not None
    # tzinfo presente = la columna es TIMESTAMPTZ, no TIMESTAMP sin zona.
    assert fila.created_at.tzinfo is not None

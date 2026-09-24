"""Constraints de users, companies y company_memberships (Paso 1.3).

Prueban la base de datos, no la aplicación: son las garantías que siguen en pie
aunque un bug del servicio intente escribir algo inválido, o aunque el migrador
legacy de Fase 5 inserte por SQL directo.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


async def _crear_usuario(session: AsyncSession, email: str) -> str:
    resultado = await session.execute(
        text("""
            INSERT INTO users (email, password_hash, first_name, last_name, status)
            VALUES (:email, 'hash-de-prueba', 'Nombre', 'Apellido', 'ACTIVE')
            RETURNING id
        """),
        {"email": email},
    )
    return str(resultado.scalar_one())


async def _crear_empresa(session: AsyncSession, legal_name: str, tax_id: str | None = None) -> str:
    resultado = await session.execute(
        text("""
            INSERT INTO companies (legal_name, tax_id, status)
            VALUES (:legal_name, :tax_id, 'ACTIVE')
            RETURNING id
        """),
        {"legal_name": legal_name, "tax_id": tax_id},
    )
    return str(resultado.scalar_one())


class TestUsers:
    async def test_email_unico_ignora_mayusculas(self, session: AsyncSession) -> None:
        """CITEXT: dos emails que solo difieren en capitalización son el mismo.

        Es lo que impide que 'Cliente@x.com' y 'cliente@x.com' terminen siendo
        dos cuentas distintas si la aplicación olvida normalizar.
        """
        await _crear_usuario(session, "Cliente@Amvarmar.com")

        with pytest.raises(IntegrityError):
            await _crear_usuario(session, "cliente@amvarmar.com")

    async def test_status_invalido_rechazado(self, session: AsyncSession) -> None:
        with pytest.raises(IntegrityError):
            await session.execute(
                text("""
                    INSERT INTO users (email, password_hash, first_name, last_name, status)
                    VALUES ('x@amvarmar.com', 'h', 'N', 'A', 'ELIMINADO')
                """)
            )

    async def test_intentos_fallidos_no_puede_ser_negativo(self, session: AsyncSession) -> None:
        user_id = await _crear_usuario(session, "intentos@amvarmar.com")

        with pytest.raises(IntegrityError):
            await session.execute(
                text("UPDATE users SET failed_login_attempts = -1 WHERE id = :id"),
                {"id": user_id},
            )

    async def test_authz_version_arranca_en_uno_y_debe_ser_positiva(
        self, session: AsyncSession
    ) -> None:
        user_id = await _crear_usuario(session, "authz@amvarmar.com")

        version = (
            await session.execute(
                text("SELECT authz_version FROM users WHERE id = :id"), {"id": user_id}
            )
        ).scalar_one()
        assert version == 1

        with pytest.raises(IntegrityError):
            await session.execute(
                text("UPDATE users SET authz_version = 0 WHERE id = :id"), {"id": user_id}
            )

    async def test_no_existe_columna_is_staff(self, session: AsyncSession) -> None:
        """La autorización sale del rol, nunca de un flag en el usuario."""
        columnas = (
            await session.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name='users'")
            )
        ).scalars()
        assert "is_staff" not in set(columnas)


class TestCompanies:
    async def test_tax_id_unico_entre_empresas_activas(self, session: AsyncSession) -> None:
        await _crear_empresa(session, "Importadora Uno S.A.", tax_id="3-101-111111")

        with pytest.raises(IntegrityError):
            await _crear_empresa(session, "Importadora Dos S.A.", tax_id="3-101-111111")

    async def test_tax_id_nulo_no_colisiona(self, session: AsyncSession) -> None:
        """El índice único es parcial: varias empresas pueden no tener cédula."""
        una = await _crear_empresa(session, "Sin Cedula Uno")
        dos = await _crear_empresa(session, "Sin Cedula Dos")

        # Acotado a las dos que creó este test: contar toda la tabla daría por
        # sentado que está vacía, y basta con que otra prueba deje una empresa
        # committeada para que falle sin que nada esté roto.
        total = (
            await session.execute(
                text("""
                    SELECT count(*) FROM companies
                    WHERE tax_id IS NULL AND id IN (:una, :dos)
                """),
                {"una": una, "dos": dos},
            )
        ).scalar_one()
        assert total == 2

    async def test_empresa_borrada_libera_su_tax_id(self, session: AsyncSession) -> None:
        company_id = await _crear_empresa(session, "Vieja S.A.", tax_id="3-101-222222")
        await session.execute(
            text("UPDATE companies SET deleted_at = now() WHERE id = :id"), {"id": company_id}
        )

        # Sin error: el índice único solo aplica a empresas no borradas.
        await _crear_empresa(session, "Nueva S.A.", tax_id="3-101-222222")

    async def test_status_invalido_rechazado(self, session: AsyncSession) -> None:
        with pytest.raises(IntegrityError):
            await session.execute(
                text("""
                    INSERT INTO companies (legal_name, status)
                    VALUES ('Estado Malo S.A.', 'QUEBRADA')
                """)
            )

    async def test_no_se_puede_borrar_empresa_con_miembros(self, session: AsyncSession) -> None:
        """ON DELETE RESTRICT: nunca una cascada silenciosa sobre datos de negocio."""
        company_id = await _crear_empresa(session, "Con Miembros S.A.")
        user_id = await _crear_usuario(session, "miembro@amvarmar.com")
        await session.execute(
            text("""
                INSERT INTO company_memberships (company_id, user_id, status)
                VALUES (:company_id, :user_id, 'ACTIVE')
            """),
            {"company_id": company_id, "user_id": user_id},
        )

        with pytest.raises(IntegrityError):
            await session.execute(text("DELETE FROM companies WHERE id = :id"), {"id": company_id})


class TestCompanyMemberships:
    async def test_un_usuario_pertenece_a_una_sola_empresa(self, session: AsyncSession) -> None:
        """ADR-0011: la restricción es UNIQUE(user_id), más estricta que
        UNIQUE(company_id, user_id) del documento de arquitectura."""
        user_id = await _crear_usuario(session, "multi@amvarmar.com")
        empresa_a = await _crear_empresa(session, "Empresa A S.A.")
        empresa_b = await _crear_empresa(session, "Empresa B S.A.")

        await session.execute(
            text("""
                INSERT INTO company_memberships (company_id, user_id, status)
                VALUES (:company_id, :user_id, 'ACTIVE')
            """),
            {"company_id": empresa_a, "user_id": user_id},
        )

        with pytest.raises(IntegrityError):
            await session.execute(
                text("""
                    INSERT INTO company_memberships (company_id, user_id, status)
                    VALUES (:company_id, :user_id, 'ACTIVE')
                """),
                {"company_id": empresa_b, "user_id": user_id},
            )

    async def test_status_invalido_rechazado(self, session: AsyncSession) -> None:
        user_id = await _crear_usuario(session, "estado@amvarmar.com")
        company_id = await _crear_empresa(session, "Estados S.A.")

        with pytest.raises(IntegrityError):
            await session.execute(
                text("""
                    INSERT INTO company_memberships (company_id, user_id, status)
                    VALUES (:company_id, :user_id, 'EXPULSADO')
                """),
                {"company_id": company_id, "user_id": user_id},
            )

    async def test_staff_interno_no_necesita_membership(self, session: AsyncSession) -> None:
        """ADR-0011: el personal de AMVARMAR existe como usuario sin fila aquí.

        Tener membership es lo que define a un usuario como cliente.
        """
        user_id = await _crear_usuario(session, "ops@amvarmar.com")

        total = (
            await session.execute(
                text("SELECT count(*) FROM company_memberships WHERE user_id = :id"),
                {"id": user_id},
            )
        ).scalar_one()
        assert total == 0

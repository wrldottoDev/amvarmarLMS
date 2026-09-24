"""Login, rotación de refresh y detección de reutilización (Paso 1.6)."""

import asyncio
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security.argon2 import hash_password
from app.core.security.jwt import TokenType, decodificar, emitir_refresh_token
from app.core.security.token_fingerprint import fingerprint
from app.modules.auth.models import RevokeReason
from app.modules.auth.service import (
    CredencialesInvalidas,
    ReutilizacionDetectada,
    SesionInvalida,
    login,
    logout,
    logout_todas,
    rotar_refresh,
    sesion_activa,
)

pytestmark = pytest.mark.security

PASSWORD = "una passphrase de prueba suficientemente larga"


async def _crear_usuario(
    session: AsyncSession,
    email: str = "usuario@amvarmar.test",
    *,
    password: str = PASSWORD,
    status: str = "ACTIVE",
) -> UUID:
    return (
        await session.execute(
            text("""
                INSERT INTO users (email, password_hash, first_name, last_name, status)
                VALUES (:email, :hash, 'Nombre', 'Apellido', :status)
                RETURNING id
            """),
            {"email": email, "hash": hash_password(password), "status": status},
        )
    ).scalar_one()


class TestLogin:
    async def test_login_correcto_emite_par_de_tokens(self, session: AsyncSession) -> None:
        user_id = await _crear_usuario(session)

        tokens = await login(session, email="usuario@amvarmar.test", password=PASSWORD)

        access = decodificar(tokens.access_token, TokenType.ACCESS)
        refresh = decodificar(tokens.refresh_token, TokenType.REFRESH)

        assert access.user_id == user_id
        assert access.session_id == tokens.session_id
        assert refresh.session_id == tokens.session_id

    async def test_email_es_insensible_a_mayusculas(self, session: AsyncSession) -> None:
        await _crear_usuario(session, "Usuario@Amvarmar.test")

        tokens = await login(session, email="usuario@amvarmar.test", password=PASSWORD)

        assert tokens.access_token

    async def test_password_incorrecto_falla(self, session: AsyncSession) -> None:
        await _crear_usuario(session)

        with pytest.raises(CredencialesInvalidas):
            await login(session, email="usuario@amvarmar.test", password="incorrecto")

    async def test_usuario_inexistente_falla_igual(self, session: AsyncSession) -> None:
        with pytest.raises(CredencialesInvalidas):
            await login(session, email="nadie@amvarmar.test", password=PASSWORD)

    @pytest.mark.parametrize("status", ["INVITED", "SUSPENDED", "DISABLED"])
    async def test_cuenta_no_activa_no_puede_entrar(
        self, session: AsyncSession, status: str
    ) -> None:
        await _crear_usuario(session, status=status)

        with pytest.raises(CredencialesInvalidas):
            await login(session, email="usuario@amvarmar.test", password=PASSWORD)

    async def test_cuenta_bloqueada_no_puede_entrar(self, session: AsyncSession) -> None:
        user_id = await _crear_usuario(session)
        await session.execute(
            text("UPDATE users SET locked_until = now() + interval '1 hour' WHERE id = :id"),
            {"id": user_id},
        )

        with pytest.raises(CredencialesInvalidas):
            await login(session, email="usuario@amvarmar.test", password=PASSWORD)

    async def test_password_incorrecto_incrementa_contador(self, session: AsyncSession) -> None:
        user_id = await _crear_usuario(session)

        for _ in range(3):
            with pytest.raises(CredencialesInvalidas):
                await login(session, email="usuario@amvarmar.test", password="mal")

        intentos = (
            await session.execute(
                text("SELECT failed_login_attempts FROM users WHERE id = :id"), {"id": user_id}
            )
        ).scalar_one()
        assert intentos == 3

    async def test_login_correcto_reinicia_el_contador(self, session: AsyncSession) -> None:
        user_id = await _crear_usuario(session)
        with pytest.raises(CredencialesInvalidas):
            await login(session, email="usuario@amvarmar.test", password="mal")

        await login(session, email="usuario@amvarmar.test", password=PASSWORD)

        fila = (
            await session.execute(
                text("SELECT failed_login_attempts, last_login_at FROM users WHERE id = :id"),
                {"id": user_id},
            )
        ).one()
        assert fila.failed_login_attempts == 0
        assert fila.last_login_at is not None

    async def test_usuario_borrado_no_puede_entrar(self, session: AsyncSession) -> None:
        user_id = await _crear_usuario(session)
        await session.execute(
            text("UPDATE users SET deleted_at = now() WHERE id = :id"), {"id": user_id}
        )

        with pytest.raises(CredencialesInvalidas):
            await login(session, email="usuario@amvarmar.test", password=PASSWORD)

    async def test_login_legacy_rehashea_a_argon2(self, session: AsyncSession) -> None:
        """El hash PBKDF2 de Django se reemplaza en la misma transacción."""
        hash_legacy = (
            "pbkdf2_sha256$720000$legacytestsalt2026$o2IbQuMq1RMZAl78zfC8CUtGy+TmtNPyP8wwZCKjmJs="
        )
        password_legacy = "contrasena legacy de prueba 2026"
        user_id = (
            await session.execute(
                text("""
                    INSERT INTO users (email, password_hash, first_name, last_name, status)
                    VALUES ('legacy@amvarmar.test', :hash, 'L', 'U', 'ACTIVE')
                    RETURNING id
                """),
                {"hash": hash_legacy},
            )
        ).scalar_one()

        await login(session, email="legacy@amvarmar.test", password=password_legacy)

        fila = (
            await session.execute(
                text("SELECT password_hash, password_changed_at FROM users WHERE id = :id"),
                {"id": user_id},
            )
        ).one()
        assert fila.password_hash.startswith("$argon2id$")
        assert fila.password_changed_at is not None

    async def test_el_token_completo_no_queda_en_la_base(self, session: AsyncSession) -> None:
        """Solo se guarda el fingerprint HMAC, nunca el refresh en claro."""
        await _crear_usuario(session)
        tokens = await login(session, email="usuario@amvarmar.test", password=PASSWORD)

        # Acotado a la sesión de este login: consultar toda la tabla da por
        # sentado que está vacía, y basta con que otra prueba deje un login
        # committeado para que falle sin que nada esté roto.
        guardado = (
            await session.execute(
                text("SELECT token_hash FROM refresh_tokens WHERE session_id = :s"),
                {"s": tokens.session_id},
            )
        ).scalar_one()

        assert guardado == fingerprint(tokens.refresh_token)
        assert tokens.refresh_token.encode() not in guardado


class TestRotacion:
    async def test_rotar_emite_par_nuevo(self, session: AsyncSession) -> None:
        await _crear_usuario(session)
        primero = await login(session, email="usuario@amvarmar.test", password=PASSWORD)

        segundo = await rotar_refresh(session, primero.refresh_token)

        assert segundo.refresh_token != primero.refresh_token
        assert segundo.access_token != primero.access_token
        # Misma sesión: rotar no crea una sesión nueva.
        assert segundo.session_id == primero.session_id

    async def test_el_refresh_viejo_deja_de_servir(self, session: AsyncSession) -> None:
        await _crear_usuario(session)
        primero = await login(session, email="usuario@amvarmar.test", password=PASSWORD)
        await rotar_refresh(session, primero.refresh_token)

        with pytest.raises(ReutilizacionDetectada):
            await rotar_refresh(session, primero.refresh_token)

    async def test_reutilizar_revoca_la_sesion_completa(self, session: AsyncSession) -> None:
        """El token salió del dispositivo legítimo: se corta todo."""
        await _crear_usuario(session)
        primero = await login(session, email="usuario@amvarmar.test", password=PASSWORD)
        segundo = await rotar_refresh(session, primero.refresh_token)

        with pytest.raises(ReutilizacionDetectada):
            await rotar_refresh(session, primero.refresh_token)

        fila = (
            await session.execute(
                text("SELECT revoked_at, revoke_reason FROM auth_sessions WHERE id = :id"),
                {"id": primero.session_id},
            )
        ).one()
        assert fila.revoked_at is not None
        assert fila.revoke_reason == RevokeReason.REUSE_DETECTED

        # El token que sí era legítimo tampoco sirve ya.
        with pytest.raises(SesionInvalida):
            await rotar_refresh(session, segundo.refresh_token)

    async def test_guarda_el_linaje_de_rotaciones(self, session: AsyncSession) -> None:
        await _crear_usuario(session)
        primero = await login(session, email="usuario@amvarmar.test", password=PASSWORD)
        segundo = await rotar_refresh(session, primero.refresh_token)

        jti_primero = decodificar(primero.refresh_token, TokenType.REFRESH).jti
        jti_segundo = decodificar(segundo.refresh_token, TokenType.REFRESH).jti

        fila = (
            await session.execute(
                text("""
                    SELECT r1.replaced_by_token_id, r2.parent_token_id
                    FROM refresh_tokens r1, refresh_tokens r2
                    WHERE r1.id = :primero AND r2.id = :segundo
                """),
                {"primero": jti_primero, "segundo": jti_segundo},
            )
        ).one()
        assert fila.replaced_by_token_id == jti_segundo
        assert fila.parent_token_id == jti_primero

    async def test_refresh_de_sesion_revocada_falla(self, session: AsyncSession) -> None:
        await _crear_usuario(session)
        tokens = await login(session, email="usuario@amvarmar.test", password=PASSWORD)
        await logout(session, tokens.session_id)

        with pytest.raises(SesionInvalida):
            await rotar_refresh(session, tokens.refresh_token)

    async def test_refresh_con_token_desconocido_falla(self, session: AsyncSession) -> None:
        """Firma válida pero sin fila en la base: no se acepta."""
        token, _, _ = emitir_refresh_token(uuid4(), uuid4())

        with pytest.raises(SesionInvalida):
            await rotar_refresh(session, token)

    async def test_refresh_con_access_token_falla(self, session: AsyncSession) -> None:
        await _crear_usuario(session)
        tokens = await login(session, email="usuario@amvarmar.test", password=PASSWORD)

        with pytest.raises(SesionInvalida):
            await rotar_refresh(session, tokens.access_token)

    async def test_sesion_vencida_por_tope_absoluto_no_rota(self, session: AsyncSession) -> None:
        """El tope absoluto no se renueva por más rotaciones que haya."""
        await _crear_usuario(session)
        tokens = await login(session, email="usuario@amvarmar.test", password=PASSWORD)
        await session.execute(
            text("UPDATE auth_sessions SET absolute_expires_at = now() - interval '1 day'"),
        )

        with pytest.raises(SesionInvalida):
            await rotar_refresh(session, tokens.refresh_token)

    async def test_rotar_renueva_la_ventana_de_inactividad(self, session: AsyncSession) -> None:
        await _crear_usuario(session)
        tokens = await login(session, email="usuario@amvarmar.test", password=PASSWORD)
        await session.execute(
            text("UPDATE auth_sessions SET idle_expires_at = now() + interval '1 minute'")
        )

        await rotar_refresh(session, tokens.refresh_token)

        restante = (
            await session.execute(
                text("SELECT idle_expires_at - now() FROM auth_sessions WHERE id = :id"),
                {"id": tokens.session_id},
            )
        ).scalar_one()
        assert restante.days >= 13


class TestLogout:
    async def test_logout_revoca_la_sesion(self, session: AsyncSession) -> None:
        await _crear_usuario(session)
        tokens = await login(session, email="usuario@amvarmar.test", password=PASSWORD)

        await logout(session, tokens.session_id)

        assert await sesion_activa(session, tokens.session_id) is False

    async def test_logout_all_revoca_todas_las_sesiones(self, session: AsyncSession) -> None:
        await _crear_usuario(session)
        user_id = (
            await session.execute(
                text("SELECT id FROM users WHERE email = 'usuario@amvarmar.test'")
            )
        ).scalar_one()
        sesiones = [
            await login(session, email="usuario@amvarmar.test", password=PASSWORD) for _ in range(3)
        ]

        cerradas = await logout_todas(session, user_id)

        assert cerradas == 3
        for tokens in sesiones:
            assert await sesion_activa(session, tokens.session_id) is False

    async def test_logout_all_no_toca_a_otro_usuario(self, session: AsyncSession) -> None:
        await _crear_usuario(session, "uno@amvarmar.test")
        await _crear_usuario(session, "dos@amvarmar.test")
        user_uno = (
            await session.execute(text("SELECT id FROM users WHERE email = 'uno@amvarmar.test'"))
        ).scalar_one()
        tokens_dos = await login(session, email="dos@amvarmar.test", password=PASSWORD)
        await login(session, email="uno@amvarmar.test", password=PASSWORD)

        await logout_todas(session, user_uno)

        assert await sesion_activa(session, tokens_dos.session_id) is True

    async def test_access_token_no_sobrevive_al_logout(self, session: AsyncSession) -> None:
        """El access sigue siendo válido criptográficamente: por eso cada
        request debe consultar si la sesión sigue viva."""
        await _crear_usuario(session)
        tokens = await login(session, email="usuario@amvarmar.test", password=PASSWORD)
        claims = decodificar(tokens.access_token, TokenType.ACCESS)

        await logout(session, tokens.session_id)

        # El token se decodifica sin problema...
        assert decodificar(tokens.access_token, TokenType.ACCESS).jti == claims.jti
        # ...pero la sesión ya no existe.
        assert await sesion_activa(session, claims.session_id) is False


class TestConcurrencia:
    """Dos refresh simultáneos con el mismo token, contra conexiones reales.

    No se usan mocks: la garantía depende de que PostgreSQL serialice el
    UPDATE ... WHERE used_at IS NULL, y eso solo se puede comprobar con dos
    transacciones de verdad compitiendo.
    """

    @pytest.mark.parametrize("intento", range(20))
    async def test_solo_una_rotacion_gana(self, migrated_database: str, intento: int) -> None:
        engine = create_async_engine(migrated_database)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        email = f"concurrencia-{intento}@amvarmar.test"

        async with factory() as preparacion:
            await _crear_usuario(preparacion, email)
            tokens = await login(preparacion, email=email, password=PASSWORD)
            await preparacion.commit()

        async def rotar() -> str:
            async with factory() as s:
                try:
                    await rotar_refresh(s, tokens.refresh_token)
                    await s.commit()
                    return "ok"
                except ReutilizacionDetectada:
                    await s.commit()  # la revocación por reuse sí se persiste
                    return "reuse"
                except Exception:
                    await s.rollback()
                    return "error"

        resultados = await asyncio.gather(rotar(), rotar())

        try:
            assert sorted(resultados) == [
                "ok",
                "reuse",
            ], f"intento {intento}: se esperaba exactamente un ganador, salió {resultados}"
        finally:
            async with factory() as limpieza:
                await limpieza.execute(
                    text("DELETE FROM users WHERE email = :email"), {"email": email}
                )
                await limpieza.commit()
            await engine.dispose()

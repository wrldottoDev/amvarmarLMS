"""Redacción de auditoría e idempotencia (Paso 1.7).

La bitácora se conserva 2 años y la consultan administradores y clientes. Un
secreto que entre aquí queda expuesto todo ese tiempo, en un lugar donde nadie
lo busca.
"""

import json
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.idempotency import (
    buscar_respuesta_previa,
    guardar_respuesta,
    hash_de_solicitud,
    reservar,
)
from app.modules.audit.models import Outcome
from app.modules.audit.redaction import REDACTADO, redactar
from app.modules.audit.service import registrar

pytestmark = pytest.mark.security


class TestRedaccion:
    @pytest.mark.parametrize(
        "clave",
        [
            "password",
            "Password",
            "PASSWORD",
            "password_hash",
            "passwordHash",
            "nueva_password",
            "user_password",
            "contrasena",
            "contraseña",
            "passwd",
            "token",
            "access_token",
            "refresh_token",
            "jwt",
            "Authorization",
            "bearer_token",
            "cookie",
            "secret",
            "api_key",
            "apiKey",
            "private_key",
            "credential",
            "credenciales_smtp",
            "token_fingerprint",
            "signature",
            "firma_digital",
            "file_content",
            "contenido_archivo",
            "card_number",
            "cvv",
        ],
    )
    def test_claves_sensibles_se_redactan(self, clave: str) -> None:
        assert redactar({clave: "valor-secreto"})[clave] == REDACTADO

    def test_claves_normales_pasan_intactas(self) -> None:
        datos = {"email": "cliente@amvarmar.com", "status": "ACTIVE", "intentos": 3}

        assert redactar(datos) == datos

    def test_redacta_en_estructuras_anidadas(self) -> None:
        datos = {
            "usuario": {"email": "a@b.com", "password_hash": "$argon2id$..."},
            "sesiones": [{"id": 1, "refresh_token": "eyJhbGc..."}],
        }

        resultado = redactar(datos)

        assert resultado["usuario"]["password_hash"] == REDACTADO
        assert resultado["usuario"]["email"] == "a@b.com"
        assert resultado["sesiones"][0]["refresh_token"] == REDACTADO
        assert resultado["sesiones"][0]["id"] == 1

    def test_no_modifica_la_entrada(self) -> None:
        """El código de negocio sigue usando su objeto después de auditar."""
        original = {"password": "secreta"}

        redactar(original)

        assert original["password"] == "secreta"

    def test_estructura_muy_profunda_no_agota_la_pila(self) -> None:
        anidado: dict[str, object] = {"nivel": 0}
        actual = anidado
        for i in range(1, 60):
            siguiente: dict[str, object] = {"nivel": i}
            actual["hijo"] = siguiente
            actual = siguiente

        assert redactar(anidado) is not None


class TestAuditoriaEnBase:
    async def test_un_cambio_de_password_no_deja_el_hash_en_la_bitacora(
        self, session: AsyncSession
    ) -> None:
        """El test explícito del gate: buscar esas claves en el JSON guardado."""
        user_id = (
            await session.execute(
                text("""
                    INSERT INTO users (email, password_hash, first_name, last_name, status)
                    VALUES ('auditoria@amvarmar.com', 'hash-viejo', 'A', 'B', 'ACTIVE')
                    RETURNING id
                """)
            )
        ).scalar_one()

        await registrar(
            session,
            action="auth.password.reset",
            resource_type="user",
            resource_id=user_id,
            actor_user_id=user_id,
            before_data={"password_hash": "$argon2id$viejo", "email": "auditoria@amvarmar.com"},
            after_data={
                "password_hash": "$argon2id$nuevo",
                "password": "la-contrasena-en-claro",
                "refresh_token": "eyJhbGciOiJIUzI1NiJ9...",
            },
        )

        fila = (
            await session.execute(
                text("SELECT before_data, after_data FROM audit_logs WHERE resource_id = :id"),
                {"id": user_id},
            )
        ).one()

        crudo = json.dumps(fila.before_data) + json.dumps(fila.after_data)
        assert "argon2id" not in crudo
        assert "la-contrasena-en-claro" not in crudo
        assert "eyJhbGciOiJIUzI1NiJ9" not in crudo
        # Lo que no es secreto sí se conserva: sin eso la bitácora no sirve.
        assert fila.before_data["email"] == "auditoria@amvarmar.com"

    async def test_registra_los_campos_de_contexto(self, session: AsyncSession) -> None:
        request_id = uuid.uuid4()

        await registrar(
            session,
            action="auth.login",
            resource_type="auth_session",
            outcome=Outcome.DENIED,
            request_id=request_id,
            ip_address="203.0.113.7",
            user_agent="Mozilla/5.0",
            reason="Contraseña incorrecta",
        )

        fila = (
            await session.execute(
                text("""
                    SELECT outcome, request_id, host(ip_address) AS ip, user_agent, reason
                    FROM audit_logs WHERE request_id = :rid
                """),
                {"rid": request_id},
            )
        ).one()

        assert fila.outcome == "DENIED"
        assert fila.ip == "203.0.113.7"
        assert fila.reason == "Contraseña incorrecta"

    async def test_borrar_el_usuario_no_borra_su_rastro(self, session: AsyncSession) -> None:
        """SET NULL, no CASCADE: la auditoría sobrevive al actor."""
        user_id = (
            await session.execute(
                text("""
                    INSERT INTO users (email, password_hash, first_name, last_name, status)
                    VALUES ('efimero@amvarmar.com', 'h', 'E', 'F', 'ACTIVE')
                    RETURNING id
                """)
            )
        ).scalar_one()
        await registrar(session, action="algo.paso", resource_type="user", actor_user_id=user_id)

        await session.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})

        fila = (
            await session.execute(
                text("SELECT actor_user_id, action FROM audit_logs WHERE action = 'algo.paso'")
            )
        ).one()
        assert fila.actor_user_id is None
        assert fila.action == "algo.paso"


class TestIdempotencia:
    async def test_primera_vez_no_hay_respuesta_previa(self, session: AsyncSession) -> None:
        user_id = await _usuario(session, "idem1@amvarmar.com")

        previa = await buscar_respuesta_previa(
            session, user_id=user_id, key="k-1", request_hash=hash_de_solicitud({"a": 1})
        )

        assert previa is None

    async def test_repetir_la_clave_devuelve_la_respuesta_guardada(
        self, session: AsyncSession
    ) -> None:
        user_id = await _usuario(session, "idem2@amvarmar.com")
        huella = hash_de_solicitud({"metodo": "MARITIMO"})
        await reservar(session, user_id=user_id, key="k-2", request_hash=huella)
        await guardar_respuesta(
            session, user_id=user_id, key="k-2", status_code=201, body={"id": "abc"}
        )

        previa = await buscar_respuesta_previa(
            session, user_id=user_id, key="k-2", request_hash=huella
        )

        assert previa is not None
        assert previa.status_code == 201
        assert previa.body == {"id": "abc"}

    async def test_misma_clave_con_otro_cuerpo_da_conflicto(self, session: AsyncSession) -> None:
        """Devolver la respuesta anterior haría creer al cliente que su nueva
        petición se procesó."""
        from app.core.errors import Conflicto

        user_id = await _usuario(session, "idem3@amvarmar.com")
        await reservar(
            session, user_id=user_id, key="k-3", request_hash=hash_de_solicitud({"a": 1})
        )
        await guardar_respuesta(session, user_id=user_id, key="k-3", status_code=201, body={})

        with pytest.raises(Conflicto):
            await buscar_respuesta_previa(
                session, user_id=user_id, key="k-3", request_hash=hash_de_solicitud({"a": 2})
            )

    async def test_reservar_dos_veces_la_gana_una_sola(self, session: AsyncSession) -> None:
        user_id = await _usuario(session, "idem4@amvarmar.com")
        huella = hash_de_solicitud({})

        assert await reservar(session, user_id=user_id, key="k-4", request_hash=huella) is True
        assert await reservar(session, user_id=user_id, key="k-4", request_hash=huella) is False

    async def test_la_misma_clave_de_otro_usuario_no_colisiona(self, session: AsyncSession) -> None:
        uno = await _usuario(session, "idem5a@amvarmar.com")
        dos = await _usuario(session, "idem5b@amvarmar.com")
        huella = hash_de_solicitud({})

        assert await reservar(session, user_id=uno, key="misma", request_hash=huella) is True
        assert await reservar(session, user_id=dos, key="misma", request_hash=huella) is True

    def test_el_hash_no_depende_del_orden_de_las_claves(self) -> None:
        assert hash_de_solicitud({"a": 1, "b": 2}) == hash_de_solicitud({"b": 2, "a": 1})


async def _usuario(session: AsyncSession, email: str) -> uuid.UUID:
    return (
        await session.execute(
            text("""
                INSERT INTO users (email, password_hash, first_name, last_name, status)
                VALUES (:email, 'h', 'N', 'A', 'ACTIVE') RETURNING id
            """),
            {"email": email},
        )
    ).scalar_one()

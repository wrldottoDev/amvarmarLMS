"""Soporte del header `Idempotency-Key`.

Reintentar una creación tras un timeout de red no debe crear dos cargas ni dos
solicitudes de despacho. El cliente manda la misma clave y recibe la respuesta
original.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflicto

HEADER_IDEMPOTENCY_KEY = "Idempotency-Key"

# Ventana suficiente para cubrir reintentos de red y de usuario, sin que la
# tabla crezca indefinidamente.
TTL_HORAS = 24


@dataclass(frozen=True)
class RespuestaGuardada:
    status_code: int
    body: dict[str, Any]


def hash_de_solicitud(body: dict[str, Any] | None) -> str:
    """Huella del cuerpo, para detectar la misma clave con distinto contenido.

    `sort_keys` hace la huella estable frente al orden en que el cliente
    serialice el JSON.
    """
    serializado = json.dumps(body or {}, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(serializado.encode()).hexdigest()


async def buscar_respuesta_previa(
    session: AsyncSession,
    *,
    user_id: UUID,
    key: str,
    request_hash: str,
) -> RespuestaGuardada | None:
    """Devuelve la respuesta guardada, o None si es la primera vez.

    Si la clave existe con OTRO cuerpo, lanza `Conflicto`: devolver la respuesta
    de una operación distinta sería peor que fallar — el cliente creería que su
    nueva petición se procesó.
    """
    fila = (
        await session.execute(
            text("""
                SELECT request_hash, status_code, response_body
                FROM idempotency_keys
                WHERE user_id = :user_id AND key = :key AND expires_at > now()
            """),
            {"user_id": user_id, "key": key},
        )
    ).one_or_none()

    if fila is None:
        return None

    if fila.request_hash != request_hash:
        raise Conflicto(
            "La clave de idempotencia ya se usó con un contenido distinto.",
            code="IDEMPOTENCY_KEY_REUTILIZADA",
        )

    if fila.status_code is None:
        # Fila reservada pero sin respuesta: hay otra petición idéntica en
        # curso. Reintentar es más seguro que procesar en paralelo.
        raise Conflicto(
            "Una solicitud con esta clave está en curso. Reintente en unos segundos.",
            code="IDEMPOTENCY_EN_CURSO",
        )

    return RespuestaGuardada(status_code=fila.status_code, body=fila.response_body or {})


async def reservar(
    session: AsyncSession,
    *,
    user_id: UUID,
    key: str,
    request_hash: str,
) -> bool:
    """Reserva la clave antes de ejecutar la operación.

    Devuelve False si otra petición se adelantó — el `ON CONFLICT DO NOTHING`
    hace que la carrera la gane una sola, sin bloquear.
    """
    resultado = await session.execute(
        text("""
            INSERT INTO idempotency_keys (user_id, key, request_hash, expires_at)
            VALUES (:user_id, :key, :request_hash, :expires_at)
            ON CONFLICT (user_id, key) DO NOTHING
            RETURNING id
        """),
        {
            "user_id": user_id,
            "key": key,
            "request_hash": request_hash,
            "expires_at": datetime.now(UTC) + timedelta(hours=TTL_HORAS),
        },
    )
    return resultado.one_or_none() is not None


async def guardar_respuesta(
    session: AsyncSession,
    *,
    user_id: UUID,
    key: str,
    status_code: int,
    body: dict[str, Any],
) -> None:
    await session.execute(
        text("""
            UPDATE idempotency_keys
            SET status_code = :status_code, response_body = CAST(:body AS JSONB)
            WHERE user_id = :user_id AND key = :key
        """),
        {
            "user_id": user_id,
            "key": key,
            "status_code": status_code,
            "body": json.dumps(body, default=str, ensure_ascii=False),
        },
    )

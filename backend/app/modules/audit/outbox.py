"""Transactional outbox (Paso 4.1).

El problema que resuelve: el legacy manda correos desde `transaction.on_commit`
dentro del `save()` del modelo. Si el proceso muere entre el commit y el envío,
la notificación se pierde y nadie se entera.

Acá el evento se escribe **en la misma transacción** que el cambio de negocio.
O quedan los dos, o no queda ninguno. El worker los entrega después, fuera de
la transacción, donde una llamada lenta a un proveedor externo no puede
mantener abierta una transacción de base de datos.

Reglas que no se negocian:

- `publicar()` NO hace commit. Commitea quien esté haciendo el cambio de
  negocio; si esa transacción se revierte, el evento se va con ella.
- El payload no lleva secretos ni datos que puedan quedar viejos. Lleva
  identificadores; el worker recarga lo que necesite.
- Reclamar es `FOR UPDATE SKIP LOCKED`, no un `UPDATE ... SET status =
  'PROCESSING'`. El lock lo suelta PostgreSQL si el worker muere; una columna
  no.
"""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import obtener_logger
from app.core.observability import metrics

_log = obtener_logger("outbox")

# Cuántos intentos antes de dar el evento por perdido.
MAX_INTENTOS = 6

# Backoff exponencial con techo: 1, 2, 4, 8, 16, 30 minutos. El techo evita que
# el sexto intento caiga dentro de varias horas, cuando ya nadie lo mira.
BASE_BACKOFF_SEGUNDOS = 60
TECHO_BACKOFF_SEGUNDOS = 30 * 60


class EstadoOutbox:
    PENDING = "PENDING"
    DONE = "DONE"
    FAILED = "FAILED"


def calcular_backoff(intento: int) -> timedelta:
    """Espera antes del próximo intento. `intento` es el número ya consumido."""
    segundos = min(BASE_BACKOFF_SEGUNDOS * (2 ** max(intento - 1, 0)), TECHO_BACKOFF_SEGUNDOS)
    return timedelta(seconds=segundos)


async def publicar(
    session: AsyncSession,
    *,
    aggregate_type: str,
    aggregate_id: UUID,
    event_type: str,
    payload: dict[str, Any] | None = None,
    dedup_key: str | None = None,
) -> UUID | None:
    """Escribe un evento en el outbox. Devuelve `None` si ya existía.

    `dedup_key` es la defensa contra el doble disparo en el origen: dos
    llamadas para el mismo hecho de negocio dejan una sola fila. `ON CONFLICT
    DO NOTHING` la resuelve en la base, así que también aguanta dos procesos
    concurrentes, no solo dos llamadas del mismo.
    """
    fila = (
        await session.execute(
            text("""
                INSERT INTO outbox_events
                    (aggregate_type, aggregate_id, event_type, payload, status, dedup_key)
                VALUES (:tipo, :agregado, :evento, CAST(:payload AS JSONB), 'PENDING', :dedup)
                ON CONFLICT (dedup_key) WHERE dedup_key IS NOT NULL DO NOTHING
                RETURNING id
            """),
            {
                "tipo": aggregate_type,
                "agregado": aggregate_id,
                "evento": event_type,
                "payload": json.dumps(payload or {}, default=str, ensure_ascii=False),
                "dedup": dedup_key,
            },
        )
    ).scalar_one_or_none()

    evento_id: UUID | None = fila
    return evento_id


@dataclass(frozen=True)
class EventoPendiente:
    id: UUID
    aggregate_type: str
    aggregate_id: UUID
    event_type: str
    payload: dict[str, Any]
    attempt_count: int


@dataclass(frozen=True)
class ResultadoLote:
    entregados: int
    reintentar: int
    agotados: int


# Un manejador recibe el evento y entrega. Si lanza, se reintenta.
Manejador = Callable[[EventoPendiente], Awaitable[None]]


async def reclamar(
    session: AsyncSession, *, limite: int, ahora: datetime | None = None
) -> list[EventoPendiente]:
    """Toma un lote de eventos listos para entregar.

    `SKIP LOCKED` es lo que permite correr varios workers: cada uno se lleva
    filas distintas en vez de bloquearse esperando al otro. Las filas quedan
    bloqueadas hasta que la transacción del llamador termine.
    """
    filas = (
        await session.execute(
            text("""
                SELECT id, aggregate_type, aggregate_id, event_type, payload, attempt_count
                FROM outbox_events
                WHERE status = 'PENDING' AND available_at <= :ahora
                ORDER BY available_at, created_at
                LIMIT :limite
                FOR UPDATE SKIP LOCKED
            """),
            {"ahora": ahora or datetime.now(UTC), "limite": limite},
        )
    ).all()

    return [
        EventoPendiente(
            id=f.id,
            aggregate_type=f.aggregate_type,
            aggregate_id=f.aggregate_id,
            event_type=f.event_type,
            payload=f.payload,
            attempt_count=f.attempt_count,
        )
        for f in filas
    ]


async def marcar_entregado(session: AsyncSession, evento_id: UUID) -> None:
    await session.execute(
        text("""
            UPDATE outbox_events
            SET status = 'DONE', processed_at = now(), attempt_count = attempt_count + 1,
                last_error_code = NULL
            WHERE id = :id
        """),
        {"id": evento_id},
    )


async def marcar_fallido(
    session: AsyncSession, evento: EventoPendiente, *, error_code: str
) -> bool:
    """Registra el fallo. Devuelve `True` si el evento quedó agotado.

    Agotar los reintentos NO descarta el evento: queda en `FAILED` con el
    motivo, para que alguien pueda verlo y decidir. Un evento que desaparece en
    silencio es exactamente el problema que este paso viene a resolver.
    """
    intentos = evento.attempt_count + 1
    agotado = intentos >= MAX_INTENTOS

    await session.execute(
        text("""
            UPDATE outbox_events
            SET attempt_count = :intentos,
                last_error_code = :error,
                status = CAST(:estado AS VARCHAR),
                processed_at = CASE WHEN CAST(:estado AS VARCHAR) = 'FAILED'
                                    THEN now() ELSE NULL END,
                available_at = :proximo
            WHERE id = :id
        """),
        {
            "intentos": intentos,
            "error": error_code[:80],
            "estado": EstadoOutbox.FAILED if agotado else EstadoOutbox.PENDING,
            "proximo": datetime.now(UTC) + calcular_backoff(intentos),
            "id": evento.id,
        },
    )

    if agotado:
        _log.error(
            "outbox_evento_agotado",
            evento_id=str(evento.id),
            event_type=evento.event_type,
            intentos=intentos,
            error=error_code,
        )

    return agotado


async def procesar_lote(
    session: AsyncSession, manejador: Manejador, *, limite: int = 50
) -> ResultadoLote:
    """Reclama un lote y lo entrega, evento por evento.

    Cada entrega se aísla: si una falla, las demás del lote igual se procesan.
    Lo que NO se aísla es la transacción — todo el lote commitea junto, en el
    llamador. Un evento entregado cuyo `DONE` no se persista se reintentaría, y
    por eso el manejador tiene que tolerar recibir el mismo evento dos veces.
    """
    eventos = await reclamar(session, limite=limite)
    entregados = reintentar = agotados = 0

    for evento in eventos:
        try:
            await manejador(evento)
        # Cualquier fallo del manejador se reintenta: un proveedor externo
        # puede romper de formas que no vale la pena enumerar.
        except Exception as error:
            codigo = type(error).__name__
            if await marcar_fallido(session, evento, error_code=codigo):
                agotados += 1
                metrics.outbox_procesado_total.labels(resultado="agotado").inc()
            else:
                reintentar += 1
                metrics.outbox_procesado_total.labels(resultado="reintentar").inc()
            continue

        await marcar_entregado(session, evento.id)
        entregados += 1
        metrics.outbox_procesado_total.labels(resultado="entregado").inc()

    return ResultadoLote(entregados=entregados, reintentar=reintentar, agotados=agotados)

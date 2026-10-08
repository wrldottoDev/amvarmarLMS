"""Historial de AMVI y lo que aprende de él (ADR-0012, enmienda 2026-10-08).

- Cada turno se guarda en la conversación del usuario, para retomarla después.
- 👍/👎 por respuesta. Lo mal calificado, y lo que AMVI no supo contestar
  por falta de guía, Operaciones lo revisa en la pantalla de aprendizaje y
  escribe una guía nueva, que AMVI usa desde ese momento.

AMVI no se reentrena: mejora porque las guías mejoran, con una persona en el
medio que decide qué es correcto.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import RecursoNoEncontrado
from app.modules.copilot.base_de_conocimiento import EntradaConocimiento

DIAS_DE_RETENCION = 180
_LARGO_TITULO = 80


@dataclass(frozen=True)
class ConversacionResumen:
    id: UUID
    client_key: str
    title: str
    updated_at: datetime


@dataclass(frozen=True)
class MensajeGuardado:
    id: UUID
    role: str
    content: str
    feedback: int | None
    created_at: datetime


async def guardar_pregunta(
    session: AsyncSession,
    *,
    user_id: UUID,
    company_id: UUID | None,
    client_key: str,
    texto: str,
) -> UUID:
    """Crea la conversación si es la primera pregunta, y guarda la pregunta.
    El título es el comienzo de la primera pregunta: así se reconoce en la
    lista sin pedirle un nombre a nadie."""
    conversacion: UUID = (
        await session.execute(
            text("""
                INSERT INTO copilot_conversations (user_id, company_id, client_key, title)
                VALUES (:u, :c, :k, :t)
                ON CONFLICT (user_id, client_key) DO UPDATE SET updated_at = now()
                RETURNING id
            """),
            {"u": user_id, "c": company_id, "k": client_key, "t": _titulo(texto)},
        )
    ).scalar_one()
    await _guardar(session, conversacion, "user", texto)
    return conversacion


async def guardar_respuesta(session: AsyncSession, conversacion: UUID, texto: str) -> UUID:
    return await _guardar(session, conversacion, "assistant", texto)


async def _guardar(session: AsyncSession, conversacion: UUID, rol: str, texto: str) -> UUID:
    mensaje: UUID = (
        await session.execute(
            text("""
                INSERT INTO copilot_messages (conversation_id, role, content)
                VALUES (:c, :r, :t) RETURNING id
            """),
            {"c": conversacion, "r": rol, "t": texto},
        )
    ).scalar_one()
    return mensaje


def _titulo(texto: str) -> str:
    limpio = " ".join(texto.split())
    return limpio if len(limpio) <= _LARGO_TITULO else limpio[: _LARGO_TITULO - 1] + "…"


async def listar(session: AsyncSession, *, user_id: UUID) -> list[ConversacionResumen]:
    filas = await session.execute(
        text("""
            SELECT id, client_key, title, updated_at FROM copilot_conversations
            WHERE user_id = :u ORDER BY updated_at DESC LIMIT 50
        """),
        {"u": user_id},
    )
    return [ConversacionResumen(**f._mapping) for f in filas]


async def mensajes(
    session: AsyncSession, *, user_id: UUID, conversacion: UUID
) -> list[MensajeGuardado]:
    """Solo las propias: la de otro usuario responde igual que una que no
    existe, para no confirmar que existe."""
    await _propia(session, user_id=user_id, conversacion=conversacion)
    filas = await session.execute(
        text("""
            SELECT id, role, content, feedback, created_at FROM copilot_messages
            WHERE conversation_id = :c ORDER BY created_at, id
        """),
        {"c": conversacion},
    )
    return [MensajeGuardado(**f._mapping) for f in filas]


async def borrar(session: AsyncSession, *, user_id: UUID, conversacion: UUID) -> None:
    await _propia(session, user_id=user_id, conversacion=conversacion)
    await session.execute(
        text("DELETE FROM copilot_conversations WHERE id = :c"), {"c": conversacion}
    )


async def _propia(session: AsyncSession, *, user_id: UUID, conversacion: UUID) -> None:
    existe = (
        await session.execute(
            text("SELECT 1 FROM copilot_conversations WHERE id = :c AND user_id = :u"),
            {"c": conversacion, "u": user_id},
        )
    ).scalar_one_or_none()
    if existe is None:
        raise RecursoNoEncontrado("Conversación no encontrada.")


async def calificar(
    session: AsyncSession,
    *,
    user_id: UUID,
    mensaje: UUID,
    valor: int,
    comentario: str | None,
) -> None:
    """Solo una respuesta de AMVI, y solo en una conversación propia."""
    calificado = (
        await session.execute(
            text("""
                UPDATE copilot_messages m
                SET feedback = :v, feedback_comment = :comentario
                FROM copilot_conversations c
                WHERE m.id = :m AND m.conversation_id = c.id
                  AND c.user_id = :u AND m.role = 'assistant'
                RETURNING m.id
            """),
            {
                "v": valor,
                "comentario": (comentario or "").strip() or None,
                "m": mensaje,
                "u": user_id,
            },
        )
    ).scalar_one_or_none()
    if calificado is None:
        raise RecursoNoEncontrado("Mensaje no encontrado.")


async def registrar_tema_sin_guia(session: AsyncSession, *, user_id: UUID, tema: str) -> None:
    await session.execute(
        text("INSERT INTO copilot_unanswered_topics (topic, user_id) VALUES (:t, :u)"),
        {"t": tema.strip()[:300], "u": user_id},
    )


async def guias_curadas(session: AsyncSession) -> tuple[EntradaConocimiento, ...]:
    """Las guías que escribió Operaciones, en el mismo formato que las de
    `conocimiento/*.md`, para que `base_de_conocimiento.buscar` las trate igual."""
    filas = await session.execute(
        text("""
            SELECT title, keywords, content FROM copilot_knowledge_entries
            WHERE is_active ORDER BY created_at
        """)
    )
    return tuple(
        EntradaConocimiento(
            titulo=f.title,
            palabras_clave=tuple(p.strip() for p in f.keywords.split(",") if p.strip()),
            cuerpo=f.content,
        )
        for f in filas
    )


async def aprendizaje(session: AsyncSession) -> dict[str, list[dict[str, Any]]]:
    """Lo que Operaciones revisa: respuestas 👎 con la pregunta que las
    originó, temas sin guía pendientes y las guías escritas."""
    mal = await session.execute(
        text("""
            SELECT m.id, m.content AS respuesta, m.feedback_comment AS comentario,
                   m.created_at,
                   (SELECT p.content FROM copilot_messages p
                    WHERE p.conversation_id = m.conversation_id AND p.role = 'user'
                      AND p.created_at <= m.created_at
                    ORDER BY p.created_at DESC, p.id DESC LIMIT 1) AS pregunta
            FROM copilot_messages m
            WHERE m.feedback = -1
            ORDER BY m.created_at DESC LIMIT 100
        """)
    )
    temas = await session.execute(
        text("""
            SELECT id, topic, created_at FROM copilot_unanswered_topics
            WHERE resolved_at IS NULL ORDER BY created_at DESC LIMIT 100
        """)
    )
    guias = await session.execute(
        text("""
            SELECT id, title, keywords, content, created_at FROM copilot_knowledge_entries
            WHERE is_active ORDER BY created_at DESC
        """)
    )
    return {
        "mal_calificadas": [dict(f._mapping) for f in mal],
        "temas_sin_guia": [dict(f._mapping) for f in temas],
        "guias": [dict(f._mapping) for f in guias],
    }


async def crear_guia(
    session: AsyncSession, *, titulo: str, palabras_clave: str, contenido: str, actor: UUID
) -> UUID:
    guia: UUID = (
        await session.execute(
            text("""
                INSERT INTO copilot_knowledge_entries (title, keywords, content, created_by)
                VALUES (:t, :k, :c, :a) RETURNING id
            """),
            {"t": titulo.strip(), "k": palabras_clave.strip(), "c": contenido.strip(), "a": actor},
        )
    ).scalar_one()
    return guia


async def desactivar_guia(session: AsyncSession, guia: UUID) -> None:
    await session.execute(
        text("UPDATE copilot_knowledge_entries SET is_active = false WHERE id = :g"), {"g": guia}
    )


async def resolver_tema(session: AsyncSession, tema: UUID) -> None:
    await session.execute(
        text("UPDATE copilot_unanswered_topics SET resolved_at = now() WHERE id = :t"),
        {"t": tema},
    )


async def purgar(session: AsyncSession, *, dias: int = DIAS_DE_RETENCION) -> int:
    """Conversaciones sin uso hace más de `dias`. Los mensajes caen en cascada."""
    borradas = await session.execute(
        text("""
            DELETE FROM copilot_conversations
            WHERE updated_at < now() - make_interval(days => :d)
        """),
        {"d": dias},
    )
    return int(borradas.rowcount or 0)  # type: ignore[attr-defined]

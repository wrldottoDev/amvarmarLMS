"""AMVI: historial de conversaciones, calificaciones, guías y temas sin guía

ADR-0012, enmienda del 2026-10-08: las conversaciones se guardan (180 días) para
retomarlas y para que Operaciones mejore las guías con lo mal calificado.

Revision ID: b10f82c55e0d
Revises: a6c3e9f12b84
Create Date: 2026-10-08 11:43:13.355965
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b10f82c55e0d"
down_revision: str | Sequence[str] | None = "a6c3e9f12b84"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "copilot_knowledge_entries",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("keywords", sa.String(length=500), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_copilot_knowledge_entries_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_copilot_knowledge_entries")),
    )
    op.create_table(
        "copilot_unanswered_topics",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("topic", sa.String(length=300), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("resolved_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_copilot_unanswered_topics_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_copilot_unanswered_topics")),
    )
    op.create_index(
        "ix_copilot_unanswered_pendientes",
        "copilot_unanswered_topics",
        ["created_at"],
        unique=False,
        postgresql_where=sa.text("resolved_at IS NULL"),
    )
    op.create_table(
        "copilot_conversations",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=True),
        sa.Column("client_key", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_copilot_conversations_company_id_companies"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_copilot_conversations_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_copilot_conversations")),
        sa.UniqueConstraint("user_id", "client_key", name="uq_copilot_conversations_usuario_clave"),
    )
    op.create_index(
        "ix_copilot_conversations_usuario_reciente",
        "copilot_conversations",
        ["user_id", "updated_at"],
        unique=False,
    )
    op.create_table(
        "copilot_messages",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("feedback", sa.SmallInteger(), nullable=True),
        sa.Column("feedback_comment", sa.String(length=1000), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('user', 'assistant')", name=op.f("ck_copilot_messages_rol_valido")
        ),
        sa.CheckConstraint(
            "feedback IS NULL OR feedback IN (-1, 1)",
            name=op.f("ck_copilot_messages_feedback_valido"),
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["copilot_conversations.id"],
            name=op.f("fk_copilot_messages_conversation_id_copilot_conversations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_copilot_messages")),
    )
    op.create_index(
        "ix_copilot_messages_conversacion",
        "copilot_messages",
        ["conversation_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_copilot_messages_mal_calificados",
        "copilot_messages",
        ["created_at"],
        unique=False,
        postgresql_where=sa.text("feedback = -1"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_copilot_messages_mal_calificados",
        table_name="copilot_messages",
        postgresql_where=sa.text("feedback = -1"),
    )
    op.drop_index("ix_copilot_messages_conversacion", table_name="copilot_messages")
    op.drop_table("copilot_messages")
    op.drop_index("ix_copilot_conversations_usuario_reciente", table_name="copilot_conversations")
    op.drop_table("copilot_conversations")
    op.drop_index(
        "ix_copilot_unanswered_pendientes",
        table_name="copilot_unanswered_topics",
        postgresql_where=sa.text("resolved_at IS NULL"),
    )
    op.drop_table("copilot_unanswered_topics")
    op.drop_table("copilot_knowledge_entries")

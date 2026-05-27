"""brain chat — conversation sessions + messages for the Matrix Brain

The Brain is the Opus-4.7 "ask anything" agent. Conversation turns are
transactional session state, not domain knowledge, so they live in plain
relational tables (SHARED tier) rather than the AGE knowledge graph — keeping
graph topology queries clean and turn-replay trivial.

`surface` distinguishes where a session originated (web | telegram | api);
`external_ref` ties a session to an external id (e.g. a Telegram chat id) so
the same conversation is continuous across messages. `tool_calls` stores a
compact trace of the read-only tools the Brain invoked for that turn.

Revision ID: 0020
Revises: 0019
Create Date: 2026-05-27
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS chat_sessions (
          id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          surface        varchar(32)  NOT NULL,
          external_ref   varchar(128),
          title          text,
          created_at     timestamptz  NOT NULL DEFAULT NOW(),
          last_active_at timestamptz  NOT NULL DEFAULT NOW(),
          CONSTRAINT ck_chat_sessions_surface
            CHECK (surface IN ('web', 'telegram', 'api'))
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chat_sessions_surface_ref "
        "ON chat_sessions(surface, external_ref)"
    )
    op.execute("""
        CREATE TABLE IF NOT EXISTS chat_messages (
          id          bigserial PRIMARY KEY,
          session_id  uuid NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
          role        varchar(16) NOT NULL,
          content     text NOT NULL,
          tool_calls  jsonb,
          created_at  timestamptz NOT NULL DEFAULT NOW(),
          CONSTRAINT ck_chat_messages_role
            CHECK (role IN ('user', 'assistant', 'system'))
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chat_messages_session "
        "ON chat_messages(session_id, id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS chat_messages")
    op.execute("DROP TABLE IF EXISTS chat_sessions")

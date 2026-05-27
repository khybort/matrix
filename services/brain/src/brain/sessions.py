"""Conversation persistence (SHARED tier: chat_sessions / chat_messages)."""

from __future__ import annotations

from typing import Any

import asyncpg
import orjson


async def create_session(
    pool: asyncpg.Pool,
    *,
    surface: str,
    external_ref: str | None = None,
    title: str | None = None,
) -> str:
    row = await pool.fetchrow(
        "INSERT INTO chat_sessions (surface, external_ref, title) "
        "VALUES ($1, $2, $3) RETURNING id",
        surface,
        external_ref,
        title,
    )
    return str(row["id"])


async def get_or_create_by_ref(
    pool: asyncpg.Pool, *, surface: str, external_ref: str
) -> str:
    row = await pool.fetchrow(
        "SELECT id FROM chat_sessions WHERE surface = $1 AND external_ref = $2 "
        "ORDER BY last_active_at DESC LIMIT 1",
        surface,
        external_ref,
    )
    if row:
        return str(row["id"])
    return await create_session(pool, surface=surface, external_ref=external_ref)


async def load_recent(
    pool: asyncpg.Pool, session_id: str, *, limit: int = 20
) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        "SELECT role, content FROM chat_messages WHERE session_id = $1 "
        "ORDER BY id DESC LIMIT $2",
        session_id,
        limit,
    )
    return [dict(r) for r in reversed(rows)]


async def append_message(
    pool: asyncpg.Pool,
    session_id: str,
    *,
    role: str,
    content: str,
    tool_calls: list[dict] | None = None,
) -> None:
    await pool.execute(
        "INSERT INTO chat_messages (session_id, role, content, tool_calls) "
        "VALUES ($1, $2, $3, $4::jsonb)",
        session_id,
        role,
        content,
        orjson.dumps(tool_calls).decode() if tool_calls is not None else None,
    )


async def touch(pool: asyncpg.Pool, session_id: str) -> None:
    await pool.execute(
        "UPDATE chat_sessions SET last_active_at = NOW() WHERE id = $1", session_id
    )


async def get_session(pool: asyncpg.Pool, session_id: str) -> dict[str, Any] | None:
    row = await pool.fetchrow("SELECT * FROM chat_sessions WHERE id = $1", session_id)
    if not row:
        return None
    msgs = await pool.fetch(
        "SELECT role, content, tool_calls, created_at FROM chat_messages "
        "WHERE session_id = $1 ORDER BY id",
        session_id,
    )
    out = dict(row)
    out["messages"] = [dict(m) for m in msgs]
    return out

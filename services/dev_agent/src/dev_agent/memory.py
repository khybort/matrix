"""Lessons store. Phase 0: text-only retrieval. Phase 0+: embedding-based.

All lessons enter as 'draft'. The operator promotes to 'active' explicitly.
"""

from __future__ import annotations

from typing import Any

import asyncpg


async def write_lesson_draft(
    pool: asyncpg.Pool,
    *,
    source: str,
    topic: str,
    summary: str,
    anti_pattern: str | None,
    correct_approach: str,
    relevant_paths: list[str],
    origin_task_id: int | None,
) -> int:
    return await pool.fetchval(
        """
        INSERT INTO dev_agent_lessons
          (source, topic, summary, anti_pattern, correct_approach,
           relevant_paths, origin_task_id, status)
        VALUES ($1,$2,$3,$4,$5,$6,$7,'draft')
        RETURNING id
        """,
        source, topic, summary, anti_pattern, correct_approach,
        relevant_paths, origin_task_id,
    )


async def approve_lesson(pool: asyncpg.Pool, *, lesson_id: int, by: str) -> None:
    await pool.execute(
        """
        UPDATE dev_agent_lessons
        SET status='active', approved_at=NOW(), approved_by=$1
        WHERE id=$2
        """,
        by, lesson_id,
    )


async def list_active_lessons(pool: asyncpg.Pool, *, limit: int = 100) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        "SELECT * FROM dev_agent_lessons WHERE status='active' "
        "ORDER BY created_at DESC LIMIT $1",
        limit,
    )
    return [dict(r) for r in rows]


async def list_draft_lessons(pool: asyncpg.Pool, *, limit: int = 100) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        "SELECT * FROM dev_agent_lessons WHERE status='draft' "
        "ORDER BY created_at DESC LIMIT $1",
        limit,
    )
    return [dict(r) for r in rows]


async def search_lessons_text(
    pool: asyncpg.Pool, *, query: str, top_k: int = 5, paths: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Phase 0 retrieval: simple ILIKE on topic+summary, prioritized by
    relevant_paths overlap. Phase 0+ replaces this with pgvector.
    """
    paths = paths or []
    rows = await pool.fetch(
        """
        SELECT id, topic, summary, anti_pattern, correct_approach, relevant_paths,
               hit_count,
               (CASE WHEN relevant_paths && $2::text[] THEN 1 ELSE 0 END) AS path_boost
        FROM dev_agent_lessons
        WHERE status='active'
          AND (topic ILIKE '%' || $1 || '%' OR summary ILIKE '%' || $1 || '%')
        ORDER BY path_boost DESC, hit_count DESC, created_at DESC
        LIMIT $3
        """,
        query, paths, top_k,
    )
    if rows:
        ids = [r["id"] for r in rows]
        await pool.execute(
            "UPDATE dev_agent_lessons SET hit_count = hit_count + 1 "
            "WHERE id = ANY($1::bigint[])",
            ids,
        )
    return [dict(r) for r in rows]

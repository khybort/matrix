"""Lessons store. Phase 0: text-only retrieval. Phase 0+: embedding-based.

All lessons enter as 'draft'. They become 'active' either by operator
approval or automatically once the same topic has been drafted
AUTO_ACTIVATE_REPEATS times (the failure keeps recurring).
"""

from __future__ import annotations

import os
from typing import Any

import asyncpg

_STOPWORDS = frozenset("""
this that with from into when then than also have does should would could must make sure
into onto over under about after before while where which whose their there these those
task tasks file files code change changes update updates please need needs using used
""".split())

AUTO_ACTIVATE_REPEATS = int(os.environ.get("DEV_AGENT_LESSON_AUTO_ACTIVATE_REPEATS", "2"))


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
    """Phase 0 retrieval: any-token ILIKE over topic+summary+relevant_paths,
    ranked by tokens hit, then path overlap, then hit_count. The old query
    matched the WHOLE task description as one substring, so no lesson ever
    surfaced for a real multi-sentence task. Phase 0+ replaces this with pgvector.
    """
    paths = paths or []
    tokens = [t.strip(".,;:()[]{}'\"`").lower() for t in query.replace("/", " ").replace("_", " ").split()]
    tokens = [t for t in tokens if len(t) >= 4 and t not in _STOPWORDS][:16]
    if not tokens:
        tokens = [query.lower()]
    patterns = [f"%{t}%" for t in tokens]
    rows = await pool.fetch(
        """
        SELECT id, topic, summary, anti_pattern, correct_approach, relevant_paths,
               hit_count,
               (CASE WHEN relevant_paths && $2::text[] THEN 1
                     WHEN EXISTS (SELECT 1 FROM unnest(relevant_paths) rp, unnest($2::text[]) tp
                                  WHERE tp LIKE rp || '%' OR rp LIKE tp || '%') THEN 1
                     ELSE 0 END) AS path_boost,
               (SELECT count(*) FROM unnest($1::text[]) AS pat
                 WHERE topic ILIKE pat OR summary ILIKE pat
                    OR array_to_string(relevant_paths, ' ') ILIKE pat) AS token_hits
        FROM dev_agent_lessons
        WHERE status='active'
          AND (topic ILIKE ANY($1::text[]) OR summary ILIKE ANY($1::text[])
               OR array_to_string(relevant_paths, ' ') ILIKE ANY($1::text[])
               OR relevant_paths && $2::text[]
               OR EXISTS (SELECT 1 FROM unnest(relevant_paths) rp, unnest($2::text[]) tp
                          WHERE tp LIKE rp || '%' OR rp LIKE tp || '%'))
        ORDER BY path_boost DESC, token_hits DESC, hit_count DESC, created_at DESC
        LIMIT $3
        """,
        patterns, paths, top_k,
    )
    if rows:
        ids = [r["id"] for r in rows]
        await pool.execute(
            "UPDATE dev_agent_lessons SET hit_count = hit_count + 1 "
            "WHERE id = ANY($1::bigint[])",
            ids,
        )
    return [dict(r) for r in rows]


async def auto_activate_repeated(pool: asyncpg.Pool, *, min_repeats: int = AUTO_ACTIVATE_REPEATS) -> int:
    """Promote draft lessons whose topic recurred ≥ min_repeats times.

    A lesson drafted once may be noise; the same topic twice is a pattern the
    agent should see next run without waiting for a human."""
    rows = await pool.fetch(
        """
        UPDATE dev_agent_lessons SET status='active', approved_at=NOW(), approved_by='auto-repeat'
        WHERE status='draft'
          AND topic IN (SELECT topic FROM dev_agent_lessons GROUP BY topic HAVING count(*) >= $1)
        RETURNING id
        """,
        min_repeats,
    )
    return len(rows)

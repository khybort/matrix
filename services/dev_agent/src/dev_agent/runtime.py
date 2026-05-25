"""Global runtime state for dev_agent (singleton row in dev_agent_runtime)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import asyncpg


@dataclass
class RuntimeState:
    paused: bool
    pause_reason: str | None
    paused_at: datetime | None
    paused_by: str | None

    @classmethod
    async def read(cls, pool: asyncpg.Pool) -> "RuntimeState":
        row = await pool.fetchrow(
            "SELECT paused, pause_reason, paused_at, paused_by "
            "FROM dev_agent_runtime WHERE id = TRUE"
        )
        return cls(**row)


async def is_paused(pool: asyncpg.Pool) -> bool:
    return await pool.fetchval(
        "SELECT paused FROM dev_agent_runtime WHERE id = TRUE"
    )


async def pause(pool: asyncpg.Pool, *, reason: str, actor: str) -> None:
    await pool.execute(
        """
        UPDATE dev_agent_runtime
        SET paused = TRUE,
            pause_reason = $1,
            paused_at = NOW(),
            paused_by = $2
        WHERE id = TRUE
        """,
        reason, actor,
    )


async def resume(pool: asyncpg.Pool) -> None:
    await pool.execute(
        """
        UPDATE dev_agent_runtime
        SET paused = FALSE,
            pause_reason = NULL,
            paused_at = NULL,
            paused_by = NULL
        WHERE id = TRUE
        """
    )


async def beat_heartbeat(pool: asyncpg.Pool, task_id: int) -> None:
    await pool.execute(
        "UPDATE dev_tasks SET heartbeat_at = NOW() WHERE id = $1",
        task_id,
    )


async def reap_stuck_running(pool: asyncpg.Pool, max_silence_seconds: int = 120) -> int:
    """Tasks whose heartbeat hasn't updated in N seconds are marked failed.

    Returns the number of tasks reaped.
    """
    rows = await pool.fetch(
        """
        UPDATE dev_tasks
        SET status = 'failed',
            failure_reason = 'worker_crash',
            finished_at = NOW()
        WHERE status = 'running'
          AND heartbeat_at < NOW() - make_interval(secs => $1)
        RETURNING id
        """,
        max_silence_seconds,
    )
    return len(rows)

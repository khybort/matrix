"""Persist agent events to dev_task_events (streaming)."""

from __future__ import annotations

import json
from typing import Any

import asyncpg


async def write_event(
    pool: asyncpg.Pool,
    *,
    task_id: int,
    run_id: int,
    seq: int,
    event_type: str,
    payload: dict,
    cost_usd: float | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
) -> None:
    await pool.execute(
        """
        INSERT INTO dev_task_events
          (task_id, run_id, seq, event_type, payload, cost_usd, tokens_in, tokens_out)
        VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8)
        """,
        task_id, run_id, seq, event_type, json.dumps(payload),
        cost_usd, tokens_in, tokens_out,
    )

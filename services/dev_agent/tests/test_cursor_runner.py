"""Cursor runner safety integration."""

from __future__ import annotations

from pathlib import Path

import pytest

from dev_agent.cursor_runner import run_task_with_cursor
from matrix_shared.agent_runtime.runtime import AgentEvent

pytestmark = pytest.mark.asyncio


async def _fake_stream(*, prompt, system, cwd, max_turns, session_id):
    yield AgentEvent("tool_use", {
        "name": "Write",
        "params": {"file_path": "services/execution/evil.py", "content": "x"},
        "id": "1",
    })


async def test_cursor_runner_blocks_trading_path(pg_pool, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "matrix_shared.cursor_llm.cursor_agent_stream",
        _fake_stream,
    )

    task_id = await pg_pool.fetchval("""
        INSERT INTO dev_tasks (status, source, description, max_turns)
        VALUES ('running', 'manual', 'bad', 10)
        RETURNING id
    """)
    run_id = await pg_pool.fetchval("""
        INSERT INTO dev_task_runs (task_id, run_number, status)
        VALUES ($1, 1, 'running')
        RETURNING id
    """, task_id)

    result = await run_task_with_cursor(
        pool=pg_pool,
        task_id=task_id,
        run_id=run_id,
        cwd=tmp_path,
        max_turns=10,
        cost_cap_usd=5.0,
        prompt="touch execution",
        system="sys",
    )
    assert result.completed is False
    assert result.failure_reason == "trading_path_violation"

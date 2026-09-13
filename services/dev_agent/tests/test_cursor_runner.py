"""Cursor runner safety integration."""

from __future__ import annotations

from pathlib import Path

import pytest

from dev_agent.cursor_runner import run_task_with_cursor
from matrix_shared.agent_runtime.runtime import AgentEvent

pytestmark = pytest.mark.asyncio


def _fake_cursor_bin(tmp_path: Path) -> Path:
    """A stand-in `cursor` CLI: emits one stream-json editToolCall against a
    live-capital gate file, then lingers. The runner must kill it and fail the
    task with trading_path_violation."""
    script = tmp_path / "cursor"
    event = (
        '{"type":"tool_call","subtype":"started","call_id":"1",'
        '"tool_call":{"editToolCall":{"args":{"path":"services/execution/src/execution/safety.py"}}}}'
    )
    script.write_text("#!/bin/sh\n" + f"printf '%s\\n' '{event}'\n" + "sleep 5\n")
    script.chmod(0o755)
    return script


async def test_cursor_runner_blocks_trading_path(pg_pool, tmp_path, monkeypatch):
    monkeypatch.setenv("MATRIX_CURSOR_BIN", str(_fake_cursor_bin(tmp_path)))

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

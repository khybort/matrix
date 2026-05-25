"""tests/test_kill_switch.py"""

from __future__ import annotations

import asyncio

import pytest

from dev_agent.worker import process_one_task
from tests.fake_sdk import FakeEvent

pytestmark = pytest.mark.asyncio


async def test_cancel_requested_aborts_mid_run(pg_pool, tmp_path):
    task_id = await pg_pool.fetchval("""
        INSERT INTO dev_tasks (source, description, max_turns)
        VALUES ('manual', 'cancellable', 100)
        RETURNING id
    """)

    async def slow_query(prompt, options, **_):
        for i in range(5):
            yield FakeEvent(type="assistant_text", payload={"text": f"step {i}"})
            await asyncio.sleep(0.05)

    async def _cancel_soon():
        await asyncio.sleep(0.08)
        await pg_pool.execute(
            "UPDATE dev_tasks SET cancel_requested=TRUE WHERE id=$1", task_id,
        )

    await asyncio.gather(
        process_one_task(
            pool=pg_pool,
            repo_root=tmp_path / "repo",
            worktree_root=tmp_path / "wt",
            query_fn=slow_query,
        ),
        _cancel_soon(),
    )
    row = await pg_pool.fetchrow("SELECT status, failure_reason FROM dev_tasks WHERE id=$1", task_id)
    assert row["status"] == "failed"
    assert row["failure_reason"] == "user_killed"

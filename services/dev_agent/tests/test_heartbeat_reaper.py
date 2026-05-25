"""tests/test_heartbeat_reaper.py"""

from __future__ import annotations

import pytest

from dev_agent.runtime import reap_stuck_running

pytestmark = pytest.mark.asyncio


async def test_reaps_task_with_stale_heartbeat(pg_pool):
    task_id = await pg_pool.fetchval("""
        INSERT INTO dev_tasks (status, source, description, heartbeat_at)
        VALUES ('running', 'manual', 't', NOW() - INTERVAL '5 minutes')
        RETURNING id
    """)
    count = await reap_stuck_running(pg_pool, max_silence_seconds=120)
    assert count == 1
    row = await pg_pool.fetchrow(
        "SELECT status, failure_reason FROM dev_tasks WHERE id=$1", task_id,
    )
    assert row["status"] == "failed"
    assert row["failure_reason"] == "worker_crash"


async def test_does_not_reap_fresh_heartbeat(pg_pool):
    task_id = await pg_pool.fetchval("""
        INSERT INTO dev_tasks (status, source, description, heartbeat_at)
        VALUES ('running', 'manual', 't', NOW() - INTERVAL '10 seconds')
        RETURNING id
    """)
    count = await reap_stuck_running(pg_pool, max_silence_seconds=120)
    assert count == 0
    status = await pg_pool.fetchval("SELECT status FROM dev_tasks WHERE id=$1", task_id)
    assert status == "running"


async def test_does_not_reap_non_running_tasks(pg_pool):
    task_id = await pg_pool.fetchval("""
        INSERT INTO dev_tasks (status, source, description, heartbeat_at)
        VALUES ('pending', 'manual', 't', NULL)
        RETURNING id
    """)
    count = await reap_stuck_running(pg_pool, max_silence_seconds=120)
    assert count == 0

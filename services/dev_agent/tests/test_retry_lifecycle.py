"""tests/test_retry_lifecycle.py"""

from __future__ import annotations

import pytest

from dev_agent.worker import mark_needs_changes_and_requeue

pytestmark = pytest.mark.asyncio


async def test_needs_changes_creates_new_run_number(pg_pool):
    task_id = await pg_pool.fetchval("""
        INSERT INTO dev_tasks (status, source, description)
        VALUES ('awaiting_review', 'manual', 'rev1')
        RETURNING id
    """)
    await pg_pool.execute("""
        INSERT INTO dev_task_runs (task_id, run_number, status)
        VALUES ($1, 1, 'awaiting_review')
    """, task_id)

    new_run = await mark_needs_changes_and_requeue(pg_pool, task_id, notes="please redo X")
    assert new_run == 2

    row = await pg_pool.fetchrow("SELECT status, review_notes FROM dev_tasks WHERE id=$1", task_id)
    assert row["status"] == "pending"
    assert row["review_notes"] == "please redo X"

    runs = await pg_pool.fetch("SELECT run_number, status FROM dev_task_runs WHERE task_id=$1 ORDER BY run_number", task_id)
    assert [r["run_number"] for r in runs] == [1]

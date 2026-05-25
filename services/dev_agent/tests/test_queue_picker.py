"""tests/test_queue_picker.py"""

from __future__ import annotations

import pytest

from dev_agent.worker import pick_next_task

pytestmark = pytest.mark.asyncio


async def test_picker_returns_highest_priority_first(pg_pool):
    await pg_pool.execute("INSERT INTO dev_tasks (source, description, priority) VALUES ('manual','low',0)")
    high_id = await pg_pool.fetchval(
        "INSERT INTO dev_tasks (source, description, priority) VALUES ('manual','high',10) RETURNING id"
    )
    async with pg_pool.acquire() as conn:
        async with conn.transaction():
            task = await pick_next_task(conn)
            assert task["id"] == high_id


async def test_picker_skips_running_tasks(pg_pool):
    await pg_pool.execute("INSERT INTO dev_tasks (status, source, description) VALUES ('running','manual','wip')")
    pend_id = await pg_pool.fetchval(
        "INSERT INTO dev_tasks (source, description) VALUES ('manual','next') RETURNING id"
    )
    async with pg_pool.acquire() as conn:
        async with conn.transaction():
            task = await pick_next_task(conn)
            assert task["id"] == pend_id


async def test_picker_respects_touches_files_overlap(pg_pool):
    """If a running task touches files X, a pending task that also touches X
    must not be picked. A pending task touching different files IS picked."""
    await pg_pool.execute("""
        INSERT INTO dev_tasks (status, source, description, touches_files)
        VALUES ('running', 'manual', 'wip', ARRAY['services/graph/extract.py'])
    """)
    overlapping = await pg_pool.fetchval("""
        INSERT INTO dev_tasks (source, description, touches_files)
        VALUES ('manual', 'overlap', ARRAY['services/graph/extract.py'])
        RETURNING id
    """)
    safe = await pg_pool.fetchval("""
        INSERT INTO dev_tasks (source, description, touches_files)
        VALUES ('manual', 'safe', ARRAY['services/labs/foo.py'])
        RETURNING id
    """)
    async with pg_pool.acquire() as conn:
        async with conn.transaction():
            task = await pick_next_task(conn)
            assert task is not None
            assert task["id"] == safe
            assert task["id"] != overlapping


async def test_picker_returns_none_when_empty(pg_pool):
    async with pg_pool.acquire() as conn:
        async with conn.transaction():
            task = await pick_next_task(conn)
            assert task is None


async def test_picker_respects_exclusive(pg_pool):
    """If an exclusive task is running, no pending task is picked."""
    await pg_pool.execute("""
        INSERT INTO dev_tasks (status, source, description, exclusive)
        VALUES ('running', 'manual', 'excl', TRUE)
    """)
    await pg_pool.execute("INSERT INTO dev_tasks (source, description) VALUES ('manual','blocked')")
    async with pg_pool.acquire() as conn:
        async with conn.transaction():
            task = await pick_next_task(conn)
            assert task is None

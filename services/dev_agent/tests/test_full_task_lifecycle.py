"""tests/test_full_task_lifecycle.py"""

from __future__ import annotations

from pathlib import Path

import pytest

from dev_agent.worker import process_one_task
from tests.fake_sdk import fake_query, load_scenario

pytestmark = pytest.mark.asyncio


async def test_lifecycle_happy_path_auto_merge(pg_pool, tmp_path):
    """Default review_mode='auto': pending → running → merged (reviewed_by='auto')."""
    task_id = await pg_pool.fetchval("""
        INSERT INTO dev_tasks (source, description, max_turns, run_tests)
        VALUES ('manual', 'do a thing', 10, FALSE)
        RETURNING id
    """)
    scenario = load_scenario("simple_edit")
    async def _fake_query(prompt, options, **_):
        async for e in fake_query(prompt, options, scenario):
            yield e

    handled = await process_one_task(
        pool=pg_pool,
        repo_root=tmp_path / "repo",
        worktree_root=tmp_path / "wt",
        query_fn=_fake_query,
    )
    assert handled is True

    row = await pg_pool.fetchrow(
        "SELECT status, started_at, finished_at, reviewed_by, reviewed_at "
        "FROM dev_tasks WHERE id=$1",
        task_id,
    )
    assert row["status"] == "merged"
    assert row["reviewed_by"] == "auto"
    assert row["reviewed_at"] is not None
    assert row["started_at"] is not None
    assert row["finished_at"] is not None

    events = await pg_pool.fetch("SELECT event_type FROM dev_task_events WHERE task_id=$1", task_id)
    assert len(events) > 0


async def test_lifecycle_manual_mode_awaits_review(pg_pool, tmp_path):
    """review_mode='manual' preserves the old behavior: success → awaiting_review."""
    task_id = await pg_pool.fetchval("""
        INSERT INTO dev_tasks (source, description, max_turns, run_tests, review_mode)
        VALUES ('manual', 'do a thing', 10, FALSE, 'manual')
        RETURNING id
    """)
    scenario = load_scenario("simple_edit")
    async def _fake_query(prompt, options, **_):
        async for e in fake_query(prompt, options, scenario):
            yield e

    await process_one_task(
        pool=pg_pool,
        repo_root=tmp_path / "repo",
        worktree_root=tmp_path / "wt",
        query_fn=_fake_query,
    )

    row = await pg_pool.fetchrow(
        "SELECT status, reviewed_by FROM dev_tasks WHERE id=$1",
        task_id,
    )
    assert row["status"] == "awaiting_review"
    assert row["reviewed_by"] is None


async def test_lifecycle_trading_path_edit_allowed(pg_pool, tmp_path):
    """FORBIDDEN_PATHS=() — strategy edits complete normally."""
    task_id = await pg_pool.fetchval("""
        INSERT INTO dev_tasks (source, description, max_turns, run_tests, review_mode)
        VALUES ('manual', 'edit strategy', 10, FALSE, 'manual')
        RETURNING id
    """)
    scenario = load_scenario("trading_path_attempt")
    async def _fake_query(prompt, options, **_):
        async for e in fake_query(prompt, options, scenario):
            yield e

    await process_one_task(
        pool=pg_pool,
        repo_root=tmp_path / "repo",
        worktree_root=tmp_path / "wt",
        query_fn=_fake_query,
    )
    row = await pg_pool.fetchrow("SELECT status, failure_reason FROM dev_tasks WHERE id=$1", task_id)
    assert row["status"] == "awaiting_review"
    assert row["failure_reason"] is None


async def test_no_tasks_returns_false(pg_pool, tmp_path):
    handled = await process_one_task(
        pool=pg_pool,
        repo_root=tmp_path / "repo",
        worktree_root=tmp_path / "wt",
        query_fn=None,
    )
    assert handled is False

"""tests/test_sdk_runner.py"""

from __future__ import annotations

from pathlib import Path

import pytest

from dev_agent.sdk_runner import RunResult, run_task_with_query
from tests.fake_sdk import fake_query, load_scenario

pytestmark = pytest.mark.asyncio


async def test_simple_edit_scenario_records_all_events(pg_pool, tmp_path):
    task_id = await pg_pool.fetchval("""
        INSERT INTO dev_tasks (status, source, description, max_turns)
        VALUES ('running', 'manual', 'simple', 10)
        RETURNING id
    """)
    run_id = await pg_pool.fetchval("""
        INSERT INTO dev_task_runs (task_id, run_number, status)
        VALUES ($1, 1, 'running')
        RETURNING id
    """, task_id)

    scenario = load_scenario("simple_edit")
    result: RunResult = await run_task_with_query(
        pool=pg_pool,
        task_id=task_id,
        run_id=run_id,
        cwd=tmp_path,
        max_turns=10,
        cost_cap_usd=5.0,
        scenario=scenario,
        query_fn=fake_query,
    )

    assert result.completed is True
    assert result.failure_reason is None
    # 6 non-result events in the scenario
    events = await pg_pool.fetch(
        "SELECT event_type, payload FROM dev_task_events WHERE task_id = $1 ORDER BY seq",
        task_id,
    )
    assert len(events) == 6


async def test_trading_path_scenario_completes_when_gate_open(pg_pool, tmp_path):
    """FORBIDDEN_PATHS=() — Edit to services/strategy is not blocked."""
    task_id = await pg_pool.fetchval("""
        INSERT INTO dev_tasks (status, source, description, max_turns)
        VALUES ('running', 'manual', 'edit strategy', 10)
        RETURNING id
    """)
    run_id = await pg_pool.fetchval("""
        INSERT INTO dev_task_runs (task_id, run_number, status)
        VALUES ($1, 1, 'running')
        RETURNING id
    """, task_id)

    scenario = load_scenario("trading_path_attempt")
    result = await run_task_with_query(
        pool=pg_pool,
        task_id=task_id,
        run_id=run_id,
        cwd=tmp_path,
        max_turns=10,
        cost_cap_usd=5.0,
        scenario=scenario,
        query_fn=fake_query,
    )
    assert result.completed is True
    assert result.failure_reason is None


async def test_max_turns_exceeded_aborts(pg_pool, tmp_path):
    task_id = await pg_pool.fetchval("""
        INSERT INTO dev_tasks (status, source, description, max_turns)
        VALUES ('running', 'manual', 'm', 2)
        RETURNING id
    """)
    run_id = await pg_pool.fetchval("""
        INSERT INTO dev_task_runs (task_id, run_number, status)
        VALUES ($1, 1, 'running')
        RETURNING id
    """, task_id)

    scenario = load_scenario("max_turns_exceeded")
    result = await run_task_with_query(
        pool=pg_pool,
        task_id=task_id,
        run_id=run_id,
        cwd=tmp_path,
        max_turns=2,
        cost_cap_usd=5.0,
        scenario=scenario,
        query_fn=fake_query,
    )
    assert result.completed is False
    assert result.failure_reason == "max_turns"

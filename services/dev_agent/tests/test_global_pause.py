"""tests/test_global_pause.py — runtime singleton pause."""

from __future__ import annotations

import pytest

from dev_agent.runtime import RuntimeState, is_paused, pause, resume

pytestmark = pytest.mark.asyncio


async def test_is_paused_default_false(pg_pool):
    state = await RuntimeState.read(pg_pool)
    assert state.paused is False


async def test_pause_sets_flag_and_metadata(pg_pool):
    await pause(pg_pool, reason="too many failures", actor="ops")
    state = await RuntimeState.read(pg_pool)
    assert state.paused is True
    assert state.pause_reason == "too many failures"
    assert state.paused_by == "ops"
    assert state.paused_at is not None


async def test_resume_clears_pause(pg_pool):
    await pause(pg_pool, reason="x", actor="ops")
    await resume(pg_pool)
    state = await RuntimeState.read(pg_pool)
    assert state.paused is False
    assert state.pause_reason is None


async def test_is_paused_returns_bool(pg_pool):
    assert await is_paused(pg_pool) is False
    await pause(pg_pool, reason="x", actor="t")
    assert await is_paused(pg_pool) is True

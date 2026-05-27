"""Process-shared concurrency budget against the subscription LLM.

The subscription is flat-rate ($0/call) but rate/latency limited, so every
agent + the brain share one in-process budget to avoid throttling each other.
"""

from __future__ import annotations

import asyncio

import pytest

from matrix_shared.agent_runtime.ratelimit import AgentRateLimiter, get_rate_limiter


@pytest.mark.asyncio
async def test_limiter_caps_concurrent_slots():
    limiter = AgentRateLimiter(max_concurrency=2)
    peak = 0

    async def worker():
        nonlocal peak
        async with limiter.slot():
            peak = max(peak, limiter.in_flight)
            await asyncio.sleep(0.02)

    await asyncio.gather(*[worker() for _ in range(6)])
    assert peak == 2
    assert limiter.in_flight == 0


@pytest.mark.asyncio
async def test_slot_released_on_exception():
    limiter = AgentRateLimiter(max_concurrency=1)
    with pytest.raises(ValueError):
        async with limiter.slot():
            raise ValueError("boom")
    # The slot must be free again despite the error.
    async with limiter.slot():
        assert limiter.in_flight == 1
    assert limiter.in_flight == 0


def test_get_rate_limiter_returns_singleton():
    assert get_rate_limiter() is get_rate_limiter()

"""Cross-process LLM slots via Postgres advisory locks (shared DB)."""

from __future__ import annotations

import asyncio

import pytest

from matrix_shared.agent_runtime import ratelimit as R

pytestmark = pytest.mark.asyncio


async def test_global_slot_serialises_across_limiters(monkeypatch):
    from matrix_shared.db import reset_engines

    # The shared engine is lru-cached; one built on an earlier test's loop is
    # "attached to a different loop" here, the acquire silently degrades to the
    # local limiter and b walks in while a holds the slot. Build a fresh one.
    reset_engines()
    # Own advisory namespace: the live LLM services hold locks in the real one
    # on the same shared DB, which made this test flake by interleaving.
    monkeypatch.setattr(R, "_ADVISORY_NS", 0x5445_5354 + (id(monkeypatch) & 0xFFFF))
    monkeypatch.setattr(R, "GLOBAL_SLOTS", 1)
    monkeypatch.setattr(R, "GLOBAL_WAIT_S", 5.0)
    a, b = R.AgentRateLimiter(3), R.AgentRateLimiter(3)  # two "processes"
    order: list[str] = []
    a_in = asyncio.Event()

    async def hold(lim, name, dur):
        async with lim.slot():
            order.append(f"{name}:in")
            if name == "a":
                a_in.set()
            await asyncio.sleep(dur)
            order.append(f"{name}:out")

    try:
        t1 = asyncio.create_task(hold(a, "a", 0.8))
        # start b only once a really holds the advisory lock (connect latency
        # used to let b win the race to the slot)
        await asyncio.wait_for(a_in.wait(), 10)
        t2 = asyncio.create_task(hold(b, "b", 0.1))
        await asyncio.gather(t1, t2)
    finally:
        reset_engines()
    # b could not enter until a released the single global slot
    assert order == ["a:in", "a:out", "b:in", "b:out"]


async def test_global_layer_off_by_default(monkeypatch):
    monkeypatch.setattr(R, "GLOBAL_SLOTS", 0)
    lim = R.AgentRateLimiter(1)
    async with lim.slot():
        assert lim.in_flight == 1
    assert lim.in_flight == 0

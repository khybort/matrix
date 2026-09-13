"""Cross-process LLM slots via Postgres advisory locks (shared DB)."""

from __future__ import annotations

import asyncio

import pytest

from matrix_shared.agent_runtime import ratelimit as R

pytestmark = pytest.mark.asyncio


async def test_global_slot_serialises_across_limiters(monkeypatch):
    # Own advisory namespace: the live LLM services hold locks in the real one
    # on the same shared DB, which made this test flake by interleaving.
    monkeypatch.setattr(R, "_ADVISORY_NS", 0x5445_5354 + (id(monkeypatch) & 0xFFFF))
    monkeypatch.setattr(R, "GLOBAL_SLOTS", 1)
    monkeypatch.setattr(R, "GLOBAL_WAIT_S", 5.0)
    a, b = R.AgentRateLimiter(3), R.AgentRateLimiter(3)  # two "processes"
    order: list[str] = []

    async def hold(lim, name, dur):
        async with lim.slot():
            order.append(f"{name}:in")
            await asyncio.sleep(dur)
            order.append(f"{name}:out")

    t1 = asyncio.create_task(hold(a, "a", 0.8))
    await asyncio.sleep(0.2)
    t2 = asyncio.create_task(hold(b, "b", 0.1))
    await asyncio.gather(t1, t2)
    # b could not enter until a released the single global slot
    assert order == ["a:in", "a:out", "b:in", "b:out"]


async def test_global_layer_off_by_default(monkeypatch):
    monkeypatch.setattr(R, "GLOBAL_SLOTS", 0)
    lim = R.AgentRateLimiter(1)
    async with lim.slot():
        assert lim.in_flight == 1
    assert lim.in_flight == 0

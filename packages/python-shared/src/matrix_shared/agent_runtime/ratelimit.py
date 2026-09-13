"""Process-shared concurrency budget for subscription LLM calls.

The Claude Code subscription is flat-rate (no per-call $) but rate- and
latency-limited. When many agents (decision, reflection, labs, synthesis)
plus the brain each run multi-step tool loops, they contend for the same
account budget. A single in-process semaphore caps how many SDK tool loops
run at once so they don't throttle each other.

Cross-process coordination (docs/AUTONOMY_PLAN.md P3.3): every container
also takes one of `MATRIX_LLM_GLOBAL_SLOTS` Postgres advisory-lock slots on
the SHARED tier before a tool loop runs, so N containers × 3 local slots no
longer become 3N concurrent loops against one flat-rate account. 0 disables
the global layer; a slot wait longer than `MATRIX_LLM_GLOBAL_WAIT_S` proceeds
anyway (rate limiting must never deadlock the decision loop).
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

from loguru import logger

_ADVISORY_NS = 0x4D41_5452  # 'MATR' — namespace for the global slot locks
GLOBAL_SLOTS = int(os.environ.get("MATRIX_LLM_GLOBAL_SLOTS", "0"))
GLOBAL_WAIT_S = float(os.environ.get("MATRIX_LLM_GLOBAL_WAIT_S", "30"))


class AgentRateLimiter:
    """Caps concurrent LLM tool loops via an asyncio semaphore."""

    def __init__(self, max_concurrency: int) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        self.max_concurrency = max_concurrency
        self.in_flight = 0
        self._sem = asyncio.Semaphore(max_concurrency)

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        """Acquire one local slot (and one global advisory-lock slot when
        MATRIX_LLM_GLOBAL_SLOTS > 0) for the duration of the context."""
        await self._sem.acquire()
        self.in_flight += 1
        conn, held = None, None
        try:
            if GLOBAL_SLOTS > 0:
                conn, held = await _acquire_global_slot(GLOBAL_SLOTS, GLOBAL_WAIT_S)
            yield
        finally:
            if conn is not None:
                try:
                    if held is not None:
                        await conn.execute(_text("SELECT pg_advisory_unlock(:ns, :i)"), {"ns": _ADVISORY_NS, "i": held})
                finally:
                    await conn.close()
            self.in_flight -= 1
            self._sem.release()


from sqlalchemy import text as _text  # noqa: E402


async def _acquire_global_slot(n_slots: int, wait_s: float):
    """Try advisory locks (ns, 0..n-1) on the SHARED DB until one is ours or
    the wait budget is exhausted. Returns (conn, slot|None); the connection
    holds the session-scoped lock and must be closed by the caller."""
    try:
        from matrix_shared.db import get_shared_engine

        conn = await get_shared_engine().connect()
    except Exception as e:  # noqa: BLE001 — no DB → local limiter only
        logger.debug(f"global rate slot unavailable ({e}); local limiter only")
        return None, None
    deadline = time.monotonic() + wait_s
    try:
        while True:
            for i in range(n_slots):
                ok = (await conn.execute(_text("SELECT pg_try_advisory_lock(:ns, :i)"),
                                         {"ns": _ADVISORY_NS, "i": i})).scalar()
                if ok:
                    return conn, i
            if time.monotonic() >= deadline:
                logger.warning(f"global LLM slots ({n_slots}) busy for {wait_s:.0f}s; proceeding unthrottled")
                return conn, None
            await asyncio.sleep(0.5)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"global rate slot error ({e}); local limiter only")
        try:
            await conn.close()
        except Exception:  # noqa: BLE001
            pass
        return None, None


@lru_cache(maxsize=1)
def get_rate_limiter() -> AgentRateLimiter:
    """Process-wide singleton; concurrency from AGENT_MAX_CONCURRENCY (default 3)."""
    return AgentRateLimiter(int(os.environ.get("AGENT_MAX_CONCURRENCY", "3")))

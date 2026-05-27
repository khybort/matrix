"""Process-shared concurrency budget for subscription LLM calls.

The Claude Code subscription is flat-rate (no per-call $) but rate- and
latency-limited. When many agents (decision, reflection, labs, synthesis)
plus the brain each run multi-step tool loops, they contend for the same
account budget. A single in-process semaphore caps how many SDK tool loops
run at once so they don't throttle each other.

Cross-process coordination (multiple service containers) is out of scope
here — each container gets its own limiter; tune `AGENT_MAX_CONCURRENCY`
per service. The dominant contention is within a process (one agent's loop
fanning out), which this covers.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache


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
        """Acquire one slot for the duration of the context."""
        await self._sem.acquire()
        self.in_flight += 1
        try:
            yield
        finally:
            self.in_flight -= 1
            self._sem.release()


@lru_cache(maxsize=1)
def get_rate_limiter() -> AgentRateLimiter:
    """Process-wide singleton; concurrency from AGENT_MAX_CONCURRENCY (default 3)."""
    return AgentRateLimiter(int(os.environ.get("AGENT_MAX_CONCURRENCY", "3")))

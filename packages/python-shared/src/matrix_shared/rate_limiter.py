"""Token-bucket rate limiter for outbound broker REST calls."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from time import monotonic


@dataclass
class TokenBucket:
    rate_per_sec: float
    capacity: float
    _tokens: float = field(init=False)
    _last_refill: float = field(init=False)
    _lock: asyncio.Lock = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._tokens = self.capacity
        self._last_refill = monotonic()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = monotonic()
        elapsed = now - self._last_refill
        if elapsed <= 0:
            return
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate_per_sec)
        self._last_refill = now

    async def take(self, n: float = 1.0) -> None:
        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= n:
                    self._tokens -= n
                    return
                deficit = n - self._tokens
                wait_s = deficit / self.rate_per_sec
            await asyncio.sleep(wait_s)

    def try_take(self, n: float = 1.0) -> bool:
        self._refill()
        if self._tokens >= n:
            self._tokens -= n
            return True
        return False

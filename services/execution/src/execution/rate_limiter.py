"""Token-bucket rate limiter for outbound broker REST calls.

A general-purpose primitive. The Bybit connector uses one of these to
throttle order/balance/cancel calls under the exchange's per-second
limit; future connectors (Binance, OKX) can share the same shape.

Why a token bucket and not a fixed sleep loop?
  - Bursts are useful: market-open / circuit-recovery flurries want to
    consume the full capacity at once, then drain at the steady rate.
  - The math is local — no shared lock, no scheduler. One asyncio task
    per connector instance is all we need.

Design notes:
  - `take()` is the await-based blocking primitive; pre-Phase-5 caller
    code uses it on every outbound call.
  - `try_take()` is the non-blocking variant — useful for "if we'd have
    to wait, drop the call and log" patterns (rapid-fire bug detector).
  - We refill continuously rather than on a tick, so an idle bucket
    is at full capacity by the time something fires, not stuck on a
    pre-refill schedule.
"""

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
        """Block until `n` tokens are available, then consume them.

        Splits the wait across multiple short sleeps so cancellation
        propagates within ~one refill tick, not at the end of a multi-
        second snooze.
        """
        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= n:
                    self._tokens -= n
                    return
                deficit = n - self._tokens
                wait_s = deficit / self.rate_per_sec
            # Sleep outside the lock so concurrent takers can also see
            # refills and serialize cleanly.
            await asyncio.sleep(wait_s)

    def try_take(self, n: float = 1.0) -> bool:
        """Non-blocking variant. True if consumed, False if not enough tokens."""
        self._refill()
        if self._tokens >= n:
            self._tokens -= n
            return True
        return False

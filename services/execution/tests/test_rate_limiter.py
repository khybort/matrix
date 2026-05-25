"""TokenBucket — both blocking and non-blocking semantics must hold.

We verify the throttling primitive in isolation (no network, no DB) so
a regression here surfaces immediately. The async take() test asserts a
LOWER bound on elapsed time — the upper bound is fuzzy because asyncio
scheduling on CI hosts can be sluggish, but the deficit/refill math
must produce at least the theoretical wait.
"""

from __future__ import annotations

import time

import pytest

from execution.rate_limiter import TokenBucket

pytestmark = pytest.mark.asyncio


async def test_token_bucket_throttles():
    """rate=2/s capacity=2 → first 2 try_take succeed, 3rd fails immediately."""
    bucket = TokenBucket(rate_per_sec=2.0, capacity=2.0)
    assert bucket.try_take() is True
    assert bucket.try_take() is True
    assert bucket.try_take() is False


async def test_token_bucket_async_take_waits():
    """rate=10/s, empty bucket asked for 20 tokens → at least ~1s wait."""
    bucket = TokenBucket(rate_per_sec=10.0, capacity=10.0)
    # Drain the initial capacity first so the next take() is pure deficit math.
    assert bucket.try_take(10.0) is True

    start = time.monotonic()
    # Need 10 more tokens → 10/10/s = 1s minimum.
    await bucket.take(10.0)
    elapsed = time.monotonic() - start
    assert elapsed >= 0.9, f"expected >=0.9s, got {elapsed:.3f}s"

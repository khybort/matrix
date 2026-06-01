import asyncio
from decimal import Decimal

import pytest

from matrix_shared.bybit_v5 import BybitV5Client
from matrix_shared.rate_limiter import TokenBucket


@pytest.mark.asyncio
async def test_token_bucket_throttles():
    bucket = TokenBucket(rate_per_sec=10.0, capacity=10.0)
    t0 = asyncio.get_event_loop().time()
    for _ in range(3):
        await bucket.take()
    assert asyncio.get_event_loop().time() - t0 < 0.5


def test_bybit_client_has_rate_limiter():
    c = BybitV5Client(testnet=True, rate_limit_per_sec=5.0)
    assert c.rate_limiter.rate_per_sec == 5.0

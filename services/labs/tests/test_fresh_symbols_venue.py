"""Lab freshness reads the traded perp feed only. A fresh `binance`
funding-poller or spot-leg row under the same symbol must not make a symbol
whose bybit feed is stale look tradeable. Runs in the suite's rolled-back
transaction (conftest)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest


@pytest.mark.asyncio
async def test_freshness_ignores_other_venues():
    from matrix_shared import session_scope
    from matrix_shared.models import TickerSnapshot

    from labs.evaluate import fresh_symbols

    now = datetime.now(UTC)
    live, stale = (f"ZZ{uuid.uuid4().hex[:6].upper()}USDT" for _ in range(2))

    def snap(sym: str, exchange: str, ago_s: float) -> TickerSnapshot:
        return TickerSnapshot(exchange=exchange, symbol=sym, snapshot_ts=now - timedelta(seconds=ago_s),
                              last_price=Decimal("1"))

    async with session_scope() as s:
        s.add_all([
            snap(live, "bybit", 5),
            snap(stale, "bybit", 3600),       # the feed the features read is an hour old
            snap(stale, "binance", 3),
            snap(stale, "bybit-spot", 2),
        ])
    assert await fresh_symbols([live, stale], now) == [live]

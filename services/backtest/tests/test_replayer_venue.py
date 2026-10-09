"""The matrix_agent replayer reads the traded perp venue, like
agent.features (commit 688894b): the ticker and book tables also hold
`binance` funding-poller and `bybit-spot` / `binance-spot` rows under the same
symbol. Runs against the live local DB inside a rolled-back transaction."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest


@pytest.mark.asyncio
async def test_replayed_features_ignore_other_venues():
    from matrix_shared import session_scope
    from matrix_shared.models import OrderBookSnapshot, TickerSnapshot
    from matrix_shared.testing import db_writes_rolled_back

    from backtest.replayers.matrix_agent import _features_at

    sym = f"ZZ{uuid.uuid4().hex[:6].upper()}USDT"
    now = datetime.now(UTC)

    def tk(exchange: str, ago_s: float, *, fr: str, oi: str | None) -> TickerSnapshot:
        return TickerSnapshot(
            exchange=exchange, symbol=sym, snapshot_ts=now - timedelta(seconds=ago_s),
            last_price=Decimal("1"), funding_rate=Decimal(fr),
            open_interest=Decimal(oi) if oi is not None else None,
        )

    def ob(exchange: str, ago_s: float, bid: str, ask: str) -> OrderBookSnapshot:
        return OrderBookSnapshot(
            exchange=exchange, symbol=sym, snapshot_ts=now - timedelta(seconds=ago_s),
            bids=[[bid, "10"]], asks=[[ask, "10"]],
        )

    async with db_writes_rolled_back():
        async with session_scope() as s:
            s.add_all([
                tk("bybit", 400, fr="-0.0010", oi="1000"),
                tk("bybit", 20, fr="-0.0012", oi="1100"),
                tk("binance", 5, fr="0.0003", oi=None),
                tk("bybit-spot", 2, fr="0", oi=None),
                ob("bybit", 10, "0.999", "1.001"),
                ob("bybit-spot", 1, "0.90", "1.10"),
            ])
        f = await _features_at(sym, now)

    assert f.funding_rate == Decimal("-0.0012")
    assert f.open_interest == Decimal("1100")
    assert f.oi_delta_pct_5m == Decimal("0.1")
    assert round(f.spread_bps, 6) == Decimal("20")

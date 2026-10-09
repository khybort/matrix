"""Features read the traded perp venue, not whichever venue wrote last.

The ticker table also carries `binance` funding-poller rows (no OI) and
`bybit-spot` / `binance-spot` rows under the same symbol. Runs against the
live local DB inside a rolled-back transaction.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest


@pytest.mark.asyncio
async def test_ticker_features_ignore_other_venues(monkeypatch):
    from matrix_shared import session_scope
    from matrix_shared.models import TickerSnapshot
    from matrix_shared.testing import db_writes_rolled_back

    from agent import features as F

    async def no_graph(*a, **k):
        raise RuntimeError("graph not under test")

    monkeypatch.setattr(F, "get_remote_graph_signal", no_graph)
    monkeypatch.setattr(F, "get_asset_context", no_graph)

    sym = f"ZZ{uuid.uuid4().hex[:6].upper()}USDT"
    now = datetime.now(UTC)

    def snap(exchange: str, ago_s: float, *, fr: str, oi: str | None, px: str) -> TickerSnapshot:
        return TickerSnapshot(
            exchange=exchange, symbol=sym, snapshot_ts=now - timedelta(seconds=ago_s),
            last_price=Decimal(px), mark_price=Decimal(px), funding_rate=Decimal(fr),
            open_interest=Decimal(oi) if oi is not None else None,
        )

    async with db_writes_rolled_back():
        async with session_scope() as s:
            s.add_all([
                snap("bybit", 400, fr="-0.0010", oi="1000", px="1.00"),  # 5 min ago
                snap("bybit", 20, fr="-0.0012", oi="1100", px="1.02"),
                # Newer rows from other venues, same symbol.
                snap("binance", 5, fr="0.0003", oi=None, px="1.05"),
                snap("bybit-spot", 2, fr="0", oi=None, px="1.06"),
            ])
        f = await F.extract_symbol_features(sym)

    assert f.funding_rate == Decimal("-0.0012")
    assert f.open_interest == Decimal("1100")
    assert f.oi_delta_pct_5m == Decimal("0.1")
    assert f.price_change_pct_5m == Decimal("0.02")

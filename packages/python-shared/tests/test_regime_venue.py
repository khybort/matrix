"""The regime's funding axis reads the traded perp venue, not whichever venue
wrote last. The ticker table also carries `binance` funding-poller rows and
`bybit-spot` / `binance-spot` rows (funding 0) under the same symbol. Runs
against the live local DB inside a rolled-back transaction."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest


@pytest.fixture(autouse=True)
def _fresh_engines():
    """Engines cached on another test's event loop break with "attached to a
    different loop", and ours must not leak into the next test either."""
    from matrix_shared.db import reset_engines

    reset_engines()
    yield
    reset_engines()


@pytest.mark.asyncio
async def test_regime_funding_ignores_other_venues(monkeypatch):
    from matrix_shared import regime as R
    from matrix_shared import session_scope
    from matrix_shared.models import TickerSnapshot
    from matrix_shared.testing import db_writes_rolled_back

    seen: dict = {}

    def classify(closes, funding):
        seen["funding"] = funding
        return R.UNKNOWN_REGIME

    monkeypatch.setattr(R, "classify", classify)
    R._cache.clear()
    sym = f"ZZ{uuid.uuid4().hex[:6].upper()}USDT"
    now = datetime.now(UTC)

    def snap(exchange: str, ago_s: float, fr: str) -> TickerSnapshot:
        return TickerSnapshot(exchange=exchange, symbol=sym, snapshot_ts=now - timedelta(seconds=ago_s),
                              last_price=Decimal("1"), funding_rate=Decimal(fr))

    async with db_writes_rolled_back():
        async with session_scope() as s:
            s.add_all([
                snap("bybit", 60, "-0.0012"),
                snap("binance", 5, "0.0003"),
                snap("bybit-spot", 2, "0"),
            ])
        await R.current_regime("crypto", symbol=sym)
    R._cache.clear()
    assert seen["funding"] == pytest.approx(-0.0012)

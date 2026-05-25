"""Grid strategy behavior.

Locks in:
  - no signal when price hasn't crossed any grid line in LOOKBACK_S
  - LONG signal when prior close was above a grid line and current price
    sat below it (cross-down → buy the dip)
  - dedup: a recent grid prediction (same symbol+side) within
    DEDUP_WINDOW_S suppresses a re-emission

The band is centered on the 24h mid with width 2*DEFAULT_PRICE_BAND_PCT
(±2%), so with seed_bars(low=100, high=200) the active band is
[147, 153]. We engineer prior_close / last_close around that.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import delete

from matrix_shared import local_session_scope
from matrix_shared.models import MarketTrade
from strategy.modules.grid import DEFAULT_PRICE_BAND_PCT, Grid

from tests.conftest import TEST_SYM

pytestmark = pytest.mark.asyncio


async def _set_last_trade(price: Decimal) -> None:
    """Overwrite TEST_SYM trades with a single recent trade at `price`."""
    now = datetime.now(timezone.utc)
    async with local_session_scope() as session:
        await session.execute(delete(MarketTrade).where(MarketTrade.symbol == TEST_SYM))
        session.add(
            MarketTrade(
                id=uuid.uuid4(),
                exchange="bybit",
                exchange_trade_id=f"grid-test-{uuid.uuid4().hex[:8]}",
                symbol=TEST_SYM,
                trade_ts=now,
                side="buy",
                price=price,
                size=Decimal("1"),
            )
        )


async def _clear_trades() -> None:
    async with local_session_scope() as session:
        await session.execute(delete(MarketTrade).where(MarketTrade.symbol == TEST_SYM))


async def test_no_signal_in_neutral_band(seed_bars):
    """When price sits at the band midpoint with no fresh cross, generate()
    returns no draft."""
    # band → [147, 153] (mid=150, ±2%)
    await seed_bars(
        low=Decimal("100"),
        high=Decimal("200"),
        prior_close=Decimal("150"),
        last_close=Decimal("150"),
    )
    await _set_last_trade(Decimal("150"))

    try:
        drafts = await Grid(symbols=[TEST_SYM]).generate()
        drafts_for_sym = [d for d in drafts if d.symbol == TEST_SYM]
        assert drafts_for_sym == []
    finally:
        await _clear_trades()


async def test_long_when_price_below_grid_line(seed_bars):
    """Prior close at top of band (153), current price at bottom (147) —
    a cross down through ≥1 grid line → LONG draft expected."""
    await seed_bars(
        low=Decimal("100"),
        high=Decimal("200"),
        prior_close=Decimal("153"),
        last_close=Decimal("147"),
    )
    await _set_last_trade(Decimal("147"))

    try:
        drafts = await Grid(symbols=[TEST_SYM]).generate()
        drafts_for_sym = [d for d in drafts if d.symbol == TEST_SYM]
        assert len(drafts_for_sym) == 1
        d = drafts_for_sym[0]
        assert d.side == "long"
        assert d.entry_price_ref == Decimal("147")
        # Confidence in [0.1, 0.9]
        assert Decimal("0.1") <= d.confidence <= Decimal("0.9")
        assert d.context["n_grids"] == 10
        assert d.context["band_pct"] == str(DEFAULT_PRICE_BAND_PCT)
    finally:
        await _clear_trades()


async def test_dedup_within_window(seed_bars, seed_predictions):
    """If a grid LONG prediction for TEST_SYM was emitted 1 minute ago,
    a fresh LONG-triggering cross within DEDUP_WINDOW_S (10min) must not
    re-emit."""
    await seed_predictions(strategy_id="grid", side="long", minutes_ago=1)
    await seed_bars(
        low=Decimal("100"),
        high=Decimal("200"),
        prior_close=Decimal("153"),
        last_close=Decimal("147"),
    )
    await _set_last_trade(Decimal("147"))

    try:
        drafts = await Grid(symbols=[TEST_SYM]).generate()
        drafts_for_sym = [d for d in drafts if d.symbol == TEST_SYM]
        assert drafts_for_sym == []
    finally:
        await _clear_trades()

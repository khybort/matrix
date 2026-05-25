"""DCA strategy behavior.

Locks in:
  - emits a LONG draft per symbol when no prior DCA prediction exists and
    a recent trade is present.
  - skips emission when a recent (< INTERVAL_MINUTES) DCA prediction is
    already on file.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import delete

from matrix_shared import local_session_scope
from matrix_shared.models import MarketTrade
from strategy.modules.dca import CONFIDENCE, Dca

from tests.conftest import TEST_SYM

pytestmark = pytest.mark.asyncio


async def _set_last_trade(price: Decimal) -> None:
    now = datetime.now(timezone.utc)
    async with local_session_scope() as session:
        await session.execute(delete(MarketTrade).where(MarketTrade.symbol == TEST_SYM))
        session.add(
            MarketTrade(
                id=uuid.uuid4(),
                exchange="bybit",
                exchange_trade_id=f"dca-test-{uuid.uuid4().hex[:8]}",
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


async def test_dca_emits_when_no_prior(seed_predictions):
    """No prior DCA prediction + a fresh trade → 1 LONG draft."""
    # seed_predictions fixture purges TEST_SYM predictions on entry.
    _ = seed_predictions  # only used for its teardown side-effect
    await _set_last_trade(Decimal("42000"))

    try:
        drafts = await Dca(symbols=[TEST_SYM]).generate()
        drafts_for_sym = [d for d in drafts if d.symbol == TEST_SYM]
        assert len(drafts_for_sym) == 1
        d = drafts_for_sym[0]
        assert d.side == "long"
        assert d.confidence == CONFIDENCE
        assert d.entry_price_ref == Decimal("42000")
        assert d.horizon_seconds == 3600
    finally:
        await _clear_trades()


async def test_dca_skips_when_recent_prediction_exists(seed_predictions):
    """A DCA prediction 5min ago with INTERVAL=60min should block a new emit."""
    await seed_predictions(strategy_id="dca", side="long", minutes_ago=5)
    await _set_last_trade(Decimal("42000"))

    try:
        drafts = await Dca(symbols=[TEST_SYM]).generate()
        drafts_for_sym = [d for d in drafts if d.symbol == TEST_SYM]
        assert drafts_for_sym == []
    finally:
        await _clear_trades()

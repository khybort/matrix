"""Challenger (shadow) predictions must be booked in the `shadow` wallet under
the champion's per-strategy slot cap — never in the champion wallet."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select

TEST_LOCAL_DSN = os.environ.get("BACKTEST_TEST_LOCAL_DSN", "postgres://matrix:matrix_dev_only@localhost:5432/matrix")
TEST_SHARED_DSN = os.environ.get("BACKTEST_TEST_SHARED_DSN", "postgres://matrix:matrix_dev_only@localhost:5433/matrix_shared")
os.environ.setdefault("LOCAL_DATABASE_URL", TEST_LOCAL_DSN)
os.environ.setdefault("SHARED_DATABASE_URL", TEST_SHARED_DSN)

from matrix_shared import local_session_scope, shared_session_scope
from matrix_shared.models import MarketTrade, PaperPosition, Prediction
from matrix_shared.models.slot_config import StrategySlotConfig

from backtest.paper_trade import _open_for_market
from tests.isolated_market import TEST_ASSET as ASSET, isolated_wallets

SYM = "TEST_SHADOWUSDT"
pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def shadow_setup():
    strat = f"TEST_shadow_{uuid.uuid4().hex[:8]}"
    trade_ids: list[str] = []
    async with isolated_wallets() as (champion_id, shadow_id):
      async with shared_session_scope() as session:
        session.add(StrategySlotConfig(strategy_id=strat, asset_class=ASSET, wallet_id=champion_id,
                                       allocated_slots=1, perf_score=0.5, consecutive_losses=0))
      tid = f"shadow-test-{uuid.uuid4().hex[:8]}"
      trade_ids.append(tid)
      async with local_session_scope() as session:
        session.add(MarketTrade(id=uuid.uuid4(), exchange="bybit", exchange_trade_id=tid, symbol=SYM,
                                trade_ts=datetime.now(timezone.utc), side="buy", price=Decimal("100"), size=Decimal("1")))
      try:
        yield strat, champion_id, shadow_id
      finally:
        async with shared_session_scope() as session:
            await session.execute(delete(Prediction).where(Prediction.strategy_id == strat))
            await session.execute(delete(StrategySlotConfig).where(StrategySlotConfig.strategy_id == strat))
        async with local_session_scope() as session:
            await session.execute(delete(MarketTrade).where(MarketTrade.exchange == "bybit").where(MarketTrade.exchange_trade_id.in_(trade_ids)))


async def _seed(strat: str, *, shadow: bool) -> None:
    now = datetime.now(timezone.utc)
    async with shared_session_scope() as session:
        session.add(Prediction(id=uuid.uuid4(), strategy_id=strat, strategy_version=3, asset_class=ASSET, symbol=SYM,
                               exchange="bybit", side="long", confidence=Decimal("0.8"), horizon_seconds=300,
                               generated_at=now, entry_price_ref=Decimal("100"), close_by=now + timedelta(seconds=300),
                               status="open", context={"is_shadow": True} if shadow else {}))


async def _open_count(strat: str, wallet_id) -> int:
    async with shared_session_scope() as session:
        return (await session.execute(
            select(func.count(PaperPosition.id)).join(Prediction, Prediction.id == PaperPosition.prediction_id)
            .where(PaperPosition.wallet_id == wallet_id).where(Prediction.strategy_id == strat)
            .where(PaperPosition.status == "open"))).scalar_one()


async def test_shadow_pass_books_into_shadow_wallet_under_champion_slots(shadow_setup):
    strat, champion_id, shadow_id = shadow_setup
    for _ in range(3):
        await _seed(strat, shadow=True)
    await _open_for_market(ASSET, shadow=True)
    assert await _open_count(strat, shadow_id) == 1      # champion's 1-slot cap applies to the challenger
    assert await _open_count(strat, champion_id) == 0    # champion wallet untouched


async def test_champion_pass_ignores_shadow_predictions(shadow_setup):
    strat, champion_id, shadow_id = shadow_setup
    await _seed(strat, shadow=True)
    await _open_for_market(ASSET)
    assert await _open_count(strat, champion_id) == 0 and await _open_count(strat, shadow_id) == 0

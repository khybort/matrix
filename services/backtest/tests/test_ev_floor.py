"""EV floor gate in paper_trade._open_for_market (Lever 1, 2026-09-15).

The engine ranks candidates by risk-adjusted expected value but, before this
gate, still opened the least-bad *negative*-EV ones just to fill open slots —
manufacturing a steady fee-bleed on trades that only pay costs and go nowhere.
The floor rejects any candidate whose EV doesn't clear the market's round-trip
cost, so the book sits in cash when nothing has edge.

Runs on the synthetic TEST_ASSET (see tests/isolated_market.py): unknown to
`all_markets()`, so it never touches live wallets and round_trip_cost_pct falls
back to the 2 bps/side legacy allowance (4 bps round trip = 0.0004 floor).
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select

TEST_LOCAL_DSN = os.environ.get(
    "BACKTEST_TEST_LOCAL_DSN",
    "postgres://matrix:matrix_dev_only@localhost:5432/matrix",
)
TEST_SHARED_DSN = os.environ.get(
    "BACKTEST_TEST_SHARED_DSN",
    "postgres://matrix:matrix_dev_only@localhost:5433/matrix_shared",
)
os.environ.setdefault("LOCAL_DATABASE_URL", TEST_LOCAL_DSN)
os.environ.setdefault("SHARED_DATABASE_URL", TEST_SHARED_DSN)

from matrix_shared import local_session_scope, shared_session_scope  # noqa: E402
from matrix_shared.models import MarketTrade, PaperPosition, Prediction  # noqa: E402
from matrix_shared.models.slot_config import StrategySlotConfig  # noqa: E402

from tests.isolated_market import TEST_ASSET as ASSET, isolated_wallets  # noqa: E402

SYM = "TEST_EVUSDT"
_TRADE_TAG = f"ev-floor-{uuid.uuid4().hex[:8]}"
_seeded_trade_ids: list[str] = []


async def _seed_trade(price: str = "50000") -> None:
    trade_id = f"{_TRADE_TAG}-{datetime.now(timezone.utc).timestamp()}"
    async with local_session_scope() as session:
        session.add(MarketTrade(
            id=uuid.uuid4(),
            exchange="bybit",
            exchange_trade_id=trade_id,
            symbol=SYM,
            trade_ts=datetime.now(timezone.utc),
            side="buy",
            price=Decimal(price),
            size=Decimal("0.01"),
        ))
    _seeded_trade_ids.append(trade_id)


async def _seed_prediction(
    strategy_id: str, *, confidence: str, tp_pct: str, sl_pct: str, close_secs: int = 300
) -> uuid.UUID:
    pid = uuid.uuid4()
    now = datetime.now(timezone.utc)
    async with shared_session_scope() as session:
        session.add(Prediction(
            id=pid,
            strategy_id=strategy_id,
            strategy_version=1,
            asset_class=ASSET,
            symbol=SYM,
            exchange="bybit",
            side="long",
            confidence=Decimal(confidence),
            horizon_seconds=close_secs,
            generated_at=now,
            entry_price_ref=Decimal("50000"),
            close_by=now + timedelta(seconds=close_secs),
            status="open",
            tp_pct=Decimal(tp_pct),
            sl_pct=Decimal(sl_pct),
        ))
    return pid


@pytest_asyncio.fixture
async def floor_wallet():
    """Isolated TEST_ASSET wallet with one neutral-perf strategy slot config
    (perf_score=0.5 → edge_multiplier 1.0, so EV == modeled base_ev with no
    symbol/pair edge data). Cleans up predictions/trades afterwards."""
    strat = f"TEST_evfloor_{uuid.uuid4().hex[:8]}"
    async with isolated_wallets() as (wallet_id, _shadow_id):
        async with shared_session_scope() as session:
            session.add(StrategySlotConfig(
                strategy_id=strat, asset_class=ASSET, wallet_id=wallet_id,
                allocated_slots=5, perf_score=0.5, consecutive_losses=0,
            ))
        try:
            yield wallet_id, strat
        finally:
            async with shared_session_scope() as session:
                await session.execute(delete(Prediction).where(Prediction.strategy_id == strat))
                await session.execute(
                    delete(StrategySlotConfig).where(StrategySlotConfig.strategy_id == strat)
                )
            if _seeded_trade_ids:
                async with local_session_scope() as session:
                    await session.execute(
                        delete(MarketTrade).where(MarketTrade.exchange == "bybit").where(
                            MarketTrade.exchange_trade_id.in_(_seeded_trade_ids)
                        )
                    )


async def _open_count(wallet_id, strat) -> int:
    async with shared_session_scope() as session:
        return (await session.execute(
            select(func.count(PaperPosition.id))
            .join(Prediction, Prediction.id == PaperPosition.prediction_id)
            .where(PaperPosition.wallet_id == wallet_id)
            .where(Prediction.strategy_id == strat)
            .where(PaperPosition.status == "open")
        )).scalar_one()


@pytest.mark.asyncio
async def test_negative_ev_candidate_is_floored(floor_wallet):
    """confidence 0.5, tp 0.1% / sl 2.0% → base_ev = -0.95% < 0.04% cost floor.
    No position must open even though a slot is free."""
    wallet_id, strat = floor_wallet
    await _seed_trade()
    await _seed_prediction(strat, confidence="0.5", tp_pct="0.001", sl_pct="0.02")

    from backtest.paper_trade import _open_for_market
    await _open_for_market(ASSET)

    n = await _open_count(wallet_id, strat)
    assert n == 0, f"negative-EV candidate should be floored, but {n} opened"


@pytest.mark.asyncio
async def test_positive_ev_candidate_opens(floor_wallet):
    """confidence 0.9, tp 2.0% / sl 0.5% → base_ev = +1.75% >> 0.04% cost floor.
    The position must open."""
    wallet_id, strat = floor_wallet
    await _seed_trade()
    await _seed_prediction(strat, confidence="0.9", tp_pct="0.02", sl_pct="0.005")

    from backtest.paper_trade import _open_for_market
    await _open_for_market(ASSET)

    n = await _open_count(wallet_id, strat)
    assert n == 1, f"positive-EV candidate should open, but {n} opened"

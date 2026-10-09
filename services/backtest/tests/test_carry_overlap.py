"""One open carry per symbol (inverse + xexch + cash-and-carry share an underlying).

Runs on the synthetic TEST_ASSET wallet so the live crypto book is never touched.
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

SYM = "TEST_CARRYUSDT"
_TRADE_TAG = f"carry-overlap-{uuid.uuid4().hex[:8]}"
_seeded_trade_ids: list[str] = []
_strats: list[str] = []


async def _seed_trade() -> None:
    trade_id = f"{_TRADE_TAG}-{datetime.now(timezone.utc).timestamp()}"
    async with local_session_scope() as session:
        session.add(MarketTrade(
            id=uuid.uuid4(),
            exchange="bybit",
            exchange_trade_id=trade_id,
            symbol=SYM,
            trade_ts=datetime.now(timezone.utc),
            side="buy",
            price=Decimal("50000"),
            size=Decimal("0.01"),
        ))
    _seeded_trade_ids.append(trade_id)


async def _seed_carry_pred(strategy_id: str, side: str, *, funding: str) -> uuid.UUID:
    pid = uuid.uuid4()
    now = datetime.now(timezone.utc)
    ctx = (
        {"funding_diff_8h": funding, "xexch_short_venue": "bybit"}
        if side == "xexch_carry"
        else {"funding_rate_8h": funding}
    )
    async with shared_session_scope() as session:
        session.add(Prediction(
            id=pid,
            strategy_id=strategy_id,
            strategy_version=1,
            asset_class=ASSET,
            symbol=SYM,
            exchange="bybit",
            side=side,
            confidence=Decimal("0.9"),
            horizon_seconds=172800,
            generated_at=now,
            entry_price_ref=Decimal("50000"),
            close_by=now + timedelta(seconds=172800),
            status="open",
            context=ctx,
        ))
    return pid


@pytest_asyncio.fixture
async def carry_wallet():
    inverse = f"TEST_ic_{uuid.uuid4().hex[:8]}"
    xexch = f"TEST_xx_{uuid.uuid4().hex[:8]}"
    _strats[:] = [inverse, xexch]
    async with isolated_wallets() as (wallet_id, _shadow_id):
        async with shared_session_scope() as session:
            for sid in (inverse, xexch):
                session.add(StrategySlotConfig(
                    strategy_id=sid, asset_class=ASSET, wallet_id=wallet_id,
                    allocated_slots=4, perf_score=0.5, consecutive_losses=0,
                ))
        try:
            yield wallet_id, inverse, xexch
        finally:
            async with shared_session_scope() as session:
                await session.execute(delete(Prediction).where(Prediction.strategy_id.in_(_strats)))
                await session.execute(
                    delete(StrategySlotConfig).where(StrategySlotConfig.strategy_id.in_(_strats))
                )
            if _seeded_trade_ids:
                async with local_session_scope() as session:
                    await session.execute(
                        delete(MarketTrade).where(MarketTrade.exchange == "bybit").where(
                            MarketTrade.exchange_trade_id.in_(_seeded_trade_ids)
                        )
                    )


@pytest.mark.asyncio
async def test_same_tick_two_carries_open_only_one(carry_wallet):
    """inverse + xexch queued the same tick on one symbol → exactly one opens
    (higher EV wins because candidates are sorted before the gate)."""
    wallet_id, inverse, xexch = carry_wallet
    await _seed_trade()
    await _seed_carry_pred(inverse, "inverse_carry", funding="-0.01")  # capture 6% over 48h
    await _seed_carry_pred(xexch, "xexch_carry", funding="0.002")      # capture 1.2% over 48h

    from backtest.paper_trade import _open_for_market
    await _open_for_market(ASSET)

    async with shared_session_scope() as session:
        n = (await session.execute(
            select(func.count(PaperPosition.id))
            .where(PaperPosition.wallet_id == wallet_id)
            .where(PaperPosition.status == "open")
        )).scalar_one()
        sides = list((await session.execute(
            select(PaperPosition.side)
            .where(PaperPosition.wallet_id == wallet_id)
            .where(PaperPosition.status == "open")
        )).scalars())
    assert n == 1, f"expected 1 carry, opened {n} ({sides})"
    assert sides == ["inverse_carry"]  # larger capture, EV-sorted first


@pytest.mark.asyncio
async def test_existing_carry_blocks_second_strategy(carry_wallet):
    """An already-open inverse_carry on the symbol blocks a later xexch open."""
    wallet_id, inverse, xexch = carry_wallet
    await _seed_trade()
    pid = await _seed_carry_pred(inverse, "inverse_carry", funding="-0.01")
    async with shared_session_scope() as session:
        session.add(PaperPosition(
            id=uuid.uuid4(), prediction_id=pid, symbol=SYM, exchange="bybit",
            asset_class=ASSET, side="inverse_carry", notional_usd=Decimal("100"),
            opened_at=datetime.now(timezone.utc), opened_price=Decimal("50000"),
            status="open", wallet_id=wallet_id,
        ))
    await _seed_carry_pred(xexch, "xexch_carry", funding="0.01")

    from backtest.paper_trade import _open_for_market
    await _open_for_market(ASSET)

    async with shared_session_scope() as session:
        n = (await session.execute(
            select(func.count(PaperPosition.id))
            .where(PaperPosition.wallet_id == wallet_id)
            .where(PaperPosition.status == "open")
        )).scalar_one()
    assert n == 1, f"existing inverse must block xexch, got {n} opens"

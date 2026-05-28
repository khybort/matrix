"""Test per-strategy slot enforcement in paper_trade._open_for_market."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

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

from matrix_shared import local_session_scope, shared_session_scope
from matrix_shared.models import MarketTrade, PaperPosition, Prediction, Wallet
from matrix_shared.models.slot_config import StrategySlotConfig

SYM = "TEST_BTCUSDT"
ASSET = "crypto"


_TRADE_TAG = f"slot-test-{uuid.uuid4().hex[:8]}"
_seeded_trade_ids: list[str] = []


async def _resolve_active_wallet() -> uuid.UUID:
    """Return the wallet ID that _open_for_market will actually use."""
    async with shared_session_scope() as session:
        row = (await session.execute(
            select(Wallet)
            .where(Wallet.asset_class == ASSET)
            .order_by(Wallet.created_at.asc())
            .limit(1)
        )).scalar_one_or_none()
    if row is None:
        raise RuntimeError("No crypto wallet found; run `make migrate`")
    return row.id


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


async def _seed_prediction(strategy_id: str, close_secs: int = 300) -> uuid.UUID:
    pid = uuid.uuid4()
    async with shared_session_scope() as session:
        now = datetime.now(timezone.utc)
        session.add(Prediction(
            id=pid,
            strategy_id=strategy_id,
            strategy_version=1,
            asset_class=ASSET,
            symbol=SYM,
            exchange="bybit",
            side="long",
            confidence=Decimal("0.8"),
            horizon_seconds=close_secs,
            generated_at=now,
            entry_price_ref=Decimal("50000"),
            close_by=now + timedelta(seconds=close_secs),
            status="open",
        ))
    return pid


@pytest_asyncio.fixture
async def slotted_wallet():
    """Attach per-strategy slot configs to the wallet _open_for_market resolves,
    so the enforcement logic finds them. Cleans up after."""
    wallet_id = await _resolve_active_wallet()
    # Unique strategy IDs to avoid interfering with production slot configs
    suffix = uuid.uuid4().hex[:8]
    strat_a = f"TEST_strat_a_{suffix}"
    strat_b = f"TEST_strat_b_{suffix}"

    async with shared_session_scope() as session:
        # strat_a gets 2 slots, strat_b gets 1 slot
        session.add(StrategySlotConfig(
            strategy_id=strat_a, asset_class=ASSET, wallet_id=wallet_id,
            allocated_slots=2, perf_score=0.7, consecutive_losses=0,
        ))
        session.add(StrategySlotConfig(
            strategy_id=strat_b, asset_class=ASSET, wallet_id=wallet_id,
            allocated_slots=1, perf_score=0.3, consecutive_losses=0,
        ))

    yield wallet_id, strat_a, strat_b

    # Cleanup: positions → predictions → slot configs
    async with shared_session_scope() as session:
        # Remove positions linked to our test predictions
        pred_ids = (await session.execute(
            select(Prediction.id).where(Prediction.strategy_id.in_([strat_a, strat_b]))
        )).scalars().all()
        if pred_ids:
            await session.execute(
                delete(PaperPosition).where(PaperPosition.prediction_id.in_(pred_ids))
            )
        await session.execute(
            delete(Prediction).where(Prediction.strategy_id.in_([strat_a, strat_b]))
        )
        await session.execute(
            delete(StrategySlotConfig).where(
                StrategySlotConfig.strategy_id.in_([strat_a, strat_b])
            )
        )
    if _seeded_trade_ids:
        async with local_session_scope() as session:
            await session.execute(
                delete(MarketTrade).where(
                    MarketTrade.exchange_trade_id.in_(_seeded_trade_ids)
                )
            )


@pytest.mark.asyncio
async def test_per_strategy_slot_cap_respected(slotted_wallet):
    """strat_b has 1 slot; even with 3 predictions, only 1 position opens."""
    wallet_id, strat_a, strat_b = slotted_wallet
    await _seed_trade()

    # Seed 3 predictions for strat_b (only 1 slot available)
    for _ in range(3):
        await _seed_prediction(strat_b)

    from backtest.paper_trade import _open_for_market
    await _open_for_market(ASSET)

    async with shared_session_scope() as session:
        from sqlalchemy import func
        from matrix_shared.models import Prediction as Pred
        n = (await session.execute(
            select(func.count(PaperPosition.id))
            .join(Pred, Pred.id == PaperPosition.prediction_id)
            .where(PaperPosition.wallet_id == wallet_id)
            .where(Pred.strategy_id == strat_b)
            .where(PaperPosition.status == "open")
        )).scalar_one()

    assert n == 1, f"strat_b has 1 slot, but {n} positions were opened"


@pytest.mark.asyncio
async def test_strategy_a_unaffected_by_strategy_b_cap(slotted_wallet):
    """strat_a has 2 slots; filling strat_b's slot doesn't block strat_a."""
    wallet_id, strat_a, strat_b = slotted_wallet
    await _seed_trade()

    await _seed_prediction(strat_b)  # fills strat_b's 1 slot
    await _seed_prediction(strat_a)
    await _seed_prediction(strat_a)

    from backtest.paper_trade import _open_for_market
    await _open_for_market(ASSET)

    async with shared_session_scope() as session:
        from sqlalchemy import func
        from matrix_shared.models import Prediction as Pred
        n_a = (await session.execute(
            select(func.count(PaperPosition.id))
            .join(Pred, Pred.id == PaperPosition.prediction_id)
            .where(PaperPosition.wallet_id == wallet_id)
            .where(Pred.strategy_id == strat_a)
            .where(PaperPosition.status == "open")
        )).scalar_one()

    assert n_a == 2, f"strat_a should have opened 2 positions, got {n_a}"

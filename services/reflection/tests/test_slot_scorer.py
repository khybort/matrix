"""Unit + integration tests for the slot scorer."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

TEST_SHARED_DSN = os.environ.get(
    "REFLECTION_TEST_SHARED_DSN",
    "postgres://matrix:matrix_dev_only@localhost:5433/matrix_shared",
)
os.environ.setdefault("SHARED_DATABASE_URL", TEST_SHARED_DSN)
os.environ.setdefault("LOCAL_DATABASE_URL", TEST_SHARED_DSN)

from matrix_shared import shared_session_scope
from matrix_shared.models import (
    MutationProposal,
    Outcome,
    PaperPosition,
    Prediction,
    Wallet,
)
from matrix_shared.models.slot_config import StrategySlotConfig


# --- Pure unit tests (no DB) ---

def test_perf_score_pure_wins():
    from reflection.slot_scorer import _perf_score
    score = _perf_score(win_rate=1.0, avg_pnl_pct=0.04, total_pnl_usd=20.0)
    assert score > 0.9


def test_perf_score_pure_losses():
    from reflection.slot_scorer import _perf_score
    score = _perf_score(win_rate=0.0, avg_pnl_pct=-0.04, total_pnl_usd=-20.0)
    assert score < 0.1


def test_perf_score_rewards_high_total_pnl_with_small_edge():
    """Small per-trade edge but consistently positive total — should NOT score below 0.5.
    This is the funding_reversion bug: high win rate, small avg_pnl_pct, positive total."""
    from reflection.slot_scorer import _perf_score
    # 53% win, 0.025% avg pnl, +$10 total over 30 trades
    score = _perf_score(win_rate=0.53, avg_pnl_pct=0.00025, total_pnl_usd=10.0)
    assert score >= 0.5, f"expected >=0.5, got {score}"


def test_slots_for_score_tiers():
    from reflection.slot_scorer import _slots_for_score
    assert _slots_for_score(0.80, base_share=10) == 20  # 2x
    assert _slots_for_score(0.60, base_share=10) == 10  # 1x
    assert _slots_for_score(0.40, base_share=10) == 5   # 0.5x
    assert _slots_for_score(0.20, base_share=10) == 2   # 0.25x
    assert _slots_for_score(0.20, base_share=2) == 1    # 0.25x, floor 1


# --- Integration tests ---

STRAT = f"TEST_scorer_{uuid.uuid4().hex[:6]}"
ASSET = "crypto"


@pytest_asyncio.fixture
async def scorer_wallet():
    wid = uuid.uuid4()
    # Insert Wallet first (FK target), then StrategySlotConfig in a separate session.
    async with shared_session_scope() as session:
        session.add(Wallet(
            id=wid,
            name=f"test-scorer-{wid}",
            asset_class=ASSET,
            starting_capital_usd=Decimal("10000"),
            cash_usd=Decimal("10000"),
            locked_usd=Decimal("0"),
            max_position_pct=Decimal("0.05"),
            max_concurrent_positions=50,
            daily_loss_circuit_pct=Decimal("0.10"),
            day_start_equity=Decimal("10000"),
            day_start_at=datetime.now(timezone.utc),
        ))
    async with shared_session_scope() as session:
        session.add(StrategySlotConfig(
            strategy_id=STRAT,
            asset_class=ASSET,
            wallet_id=wid,
            allocated_slots=10,
            perf_score=0.5,
            consecutive_losses=0,
        ))
    yield wid
    async with shared_session_scope() as session:
        await session.execute(delete(MutationProposal).where(
            MutationProposal.strategy_id == STRAT
        ))
        await session.execute(delete(StrategySlotConfig).where(
            StrategySlotConfig.wallet_id == wid
        ))
        await session.execute(delete(Wallet).where(Wallet.id == wid))


async def _seed_closed_position(
    wallet_id: uuid.UUID, pnl_usd: float, notional: float = 200.0
) -> None:
    pred_id = uuid.uuid4()
    # Two-session insert: prediction first (FK target), position after.
    now = datetime.now(timezone.utc)
    async with shared_session_scope() as session:
        session.add(Prediction(
            id=pred_id,
            strategy_id=STRAT,
            strategy_version=1,
            asset_class=ASSET,
            symbol="TEST_BTCUSDT",
            exchange="bybit",
            side="long",
            confidence=Decimal("0.8"),
            horizon_seconds=60,
            entry_price_ref=Decimal("50000"),
            generated_at=now - timedelta(minutes=6),
            close_by=now - timedelta(seconds=1),
            status="closed",
        ))
    async with shared_session_scope() as session:
        session.add(PaperPosition(
            wallet_id=wallet_id,
            prediction_id=pred_id,
            symbol="TEST_BTCUSDT",
            exchange="bybit",
            asset_class=ASSET,
            side="long",
            notional_usd=Decimal(str(notional)),
            opened_at=datetime.now(timezone.utc) - timedelta(minutes=5),
            opened_price=Decimal("50000"),
            closed_at=datetime.now(timezone.utc),
            closed_price=Decimal("50100"),
            pnl_usd=Decimal(str(pnl_usd)),
            status="closed",
        ))


@pytest.mark.asyncio
async def test_auto_cut_on_consecutive_losses(scorer_wallet):
    """8 consecutive losses → allocated_slots auto-cut to 1 (threshold raised 5→8 for patience)."""
    for _ in range(8):
        await _seed_closed_position(scorer_wallet, pnl_usd=-10.0)

    from reflection.slot_scorer import score_strategy_slots
    updated = await score_strategy_slots()
    assert updated >= 1

    async with shared_session_scope() as session:
        cfg = await session.get(StrategySlotConfig, (STRAT, ASSET, scorer_wallet))
    assert cfg.allocated_slots == 1
    assert cfg.consecutive_losses == 8


@pytest.mark.asyncio
async def test_high_performance_increases_slots(scorer_wallet):
    """High win rate + positive pnl → slots should increase above initial."""
    # Force a low starting point
    async with shared_session_scope() as session:
        cfg = await session.get(StrategySlotConfig, (STRAT, ASSET, scorer_wallet))
        cfg.allocated_slots = 5

    for _ in range(30):
        await _seed_closed_position(scorer_wallet, pnl_usd=8.0)

    from reflection.slot_scorer import score_strategy_slots
    await score_strategy_slots()

    async with shared_session_scope() as session:
        cfg = await session.get(StrategySlotConfig, (STRAT, ASSET, scorer_wallet))
    assert cfg.allocated_slots > 5, "high perf should increase slots"
    assert cfg.perf_score > 0.7

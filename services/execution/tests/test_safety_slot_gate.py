"""Test Gate #7: per-strategy slot cap in should_submit_live."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import delete

TEST_SHARED_DSN = os.environ.get(
    "EXECUTION_TEST_SHARED_DSN",
    "postgres://matrix:matrix_dev_only@localhost:5433/matrix_shared",
)
os.environ.setdefault("SHARED_DATABASE_URL", TEST_SHARED_DSN)
os.environ.setdefault("LOCAL_DATABASE_URL", TEST_SHARED_DSN)
os.environ.setdefault("LIVE_EXECUTION_ENABLED", "true")
os.environ.setdefault("LIVE_CAPITAL_CAP_USD", "999999")

from matrix_shared import shared_session_scope
from matrix_shared.models import PaperPosition, PaperTradeCertificate, Prediction, Wallet
from matrix_shared.models.slot_config import StrategySlotConfig

STRATEGY = f"test_gate7_{uuid.uuid4().hex[:6]}"
ASSET = "crypto"
VERSION = 1


@pytest_asyncio.fixture
async def gate7_wallet():
    wid = uuid.uuid4()
    # Wallet must be committed before StrategySlotConfig FK can reference it.
    async with shared_session_scope() as session:
        session.add(Wallet(
            id=wid,
            name=f"test-gate7-{wid}",
            asset_class=ASSET,
            starting_capital_usd=Decimal("10000"),
            cash_usd=Decimal("10000"),
            locked_usd=Decimal("500"),
            max_position_pct=Decimal("0.10"),
            max_concurrent_positions=50,
            daily_loss_circuit_pct=Decimal("0.10"),
            day_start_equity=Decimal("10000"),
            day_start_at=datetime.now(timezone.utc),
        ))
    async with shared_session_scope() as session:
        session.add(StrategySlotConfig(
            strategy_id=STRATEGY,
            asset_class=ASSET,
            wallet_id=wid,
            allocated_slots=1,
            perf_score=0.4,
            consecutive_losses=0,
        ))
        session.add(PaperTradeCertificate(
            strategy_id=STRATEGY,
            asset_class=ASSET,
            version=VERSION,
            status="granted",
            observation_days=60,
            win_rate=Decimal("0.55"),
            avg_pnl_usd=Decimal("0.05"),
            n_outcomes=200,
            granted_at=datetime.now(timezone.utc),
            granted_by="test",
            validity_until=datetime.now(timezone.utc) + timedelta(days=365),
        ))
    yield wid
    async with shared_session_scope() as session:
        await session.execute(delete(StrategySlotConfig).where(StrategySlotConfig.wallet_id == wid))
        await session.execute(delete(PaperPosition).where(PaperPosition.wallet_id == wid))
        await session.execute(delete(PaperTradeCertificate).where(
            PaperTradeCertificate.strategy_id == STRATEGY
        ))
        await session.execute(delete(Wallet).where(Wallet.id == wid))


async def _open_position(wallet_id: uuid.UUID) -> uuid.UUID:
    """Seed one open paper position to consume a strategy slot."""
    pred_id = uuid.uuid4()
    # Prediction must be committed before PaperPosition FK can reference it.
    async with shared_session_scope() as session:
        session.add(Prediction(
            id=pred_id,
            strategy_id=STRATEGY,
            strategy_version=VERSION,
            asset_class=ASSET,
            symbol="TEST_BTCUSDT",
            exchange="bybit",
            side="long",
            confidence=Decimal("0.8"),
            horizon_seconds=300,
            generated_at=datetime.now(timezone.utc),
            close_by=datetime.now(timezone.utc) + timedelta(seconds=300),
            entry_price_ref=Decimal("50000"),
            status="open",
        ))
    async with shared_session_scope() as session:
        session.add(PaperPosition(
            wallet_id=wallet_id,
            prediction_id=pred_id,
            symbol="TEST_BTCUSDT",
            exchange="bybit",
            asset_class=ASSET,
            side="long",
            notional_usd=Decimal("200"),
            opened_at=datetime.now(timezone.utc),
            opened_price=Decimal("50000"),
            status="open",
        ))
    return pred_id


@pytest.mark.asyncio
async def test_gate7_blocks_when_strategy_at_slot_cap(gate7_wallet):
    """With 1 slot and 1 open position, Gate #7 must block the next order."""
    from execution.safety import should_submit_live
    pred_id = await _open_position(gate7_wallet)

    decision = await should_submit_live(
        strategy_id=STRATEGY,
        asset_class=ASSET,
        strategy_version=VERSION,
        intended_notional_usd=Decimal("200"),
        wallet_id=gate7_wallet,
    )

    assert not decision.allowed
    assert any("slot cap" in r for r in decision.reasons), (
        f"Expected slot cap reason, got: {decision.reasons}"
    )

    async with shared_session_scope() as session:
        await session.execute(delete(PaperPosition).where(PaperPosition.prediction_id == pred_id))
        await session.execute(delete(Prediction).where(Prediction.id == pred_id))


@pytest.mark.asyncio
async def test_gate7_allows_when_slot_available(gate7_wallet):
    """With 1 slot and no open positions, Gate #7 should not block."""
    from execution.safety import should_submit_live

    decision = await should_submit_live(
        strategy_id=STRATEGY,
        asset_class=ASSET,
        strategy_version=VERSION,
        intended_notional_usd=Decimal("200"),
        wallet_id=gate7_wallet,
    )

    # Gate #7 specifically must not block (other gates may block in test env)
    assert not any("slot cap" in r for r in decision.reasons), (
        f"Gate #7 should not block when slot is free, got: {decision.reasons}"
    )

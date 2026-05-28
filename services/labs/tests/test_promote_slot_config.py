"""Test that apply_proposal creates a StrategySlotConfig if one doesn't exist."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

TEST_SHARED_DSN = os.environ.get(
    "LABS_TEST_SHARED_DSN",
    "postgres://matrix:matrix_dev_only@localhost:5433/matrix_shared",
)
os.environ.setdefault("SHARED_DATABASE_URL", TEST_SHARED_DSN)
os.environ.setdefault("LOCAL_DATABASE_URL", TEST_SHARED_DSN)

from matrix_shared import shared_session_scope
from matrix_shared.models import MutationProposal, StrategyConfig, Wallet
from matrix_shared.models.slot_config import StrategySlotConfig


@pytest_asyncio.fixture
async def test_wallet():
    wid = uuid.uuid4()
    async with shared_session_scope() as session:
        session.add(Wallet(
            id=wid,
            name=f"test-promote-{wid}",
            asset_class="crypto",
            starting_capital_usd=Decimal("10000"),
            cash_usd=Decimal("10000"),
            locked_usd=Decimal("0"),
            max_position_pct=Decimal("0.02"),
            max_concurrent_positions=50,
            daily_loss_circuit_pct=Decimal("0.05"),
            day_start_equity=Decimal("10000"),
            day_start_at=datetime.now(timezone.utc),
        ))
    yield wid
    async with shared_session_scope() as session:
        await session.execute(
            delete(StrategySlotConfig).where(StrategySlotConfig.wallet_id == wid)
        )
        await session.execute(delete(Wallet).where(Wallet.id == wid))


@pytest_asyncio.fixture
async def strategy_and_proposal(test_wallet):
    sid = f"test_strat_{uuid.uuid4().hex[:8]}"
    async with shared_session_scope() as session:
        cfg = StrategyConfig(
            strategy_id=sid,
            asset_class="crypto",
            version=1,
            status="active",
            params={},
            rationale="test",
        )
        session.add(cfg)
        await session.flush()

        proposal = MutationProposal(
            strategy_id=sid,
            asset_class="crypto",
            from_version=1,
            to_version=2,
            proposal_type="lab_promotion",
            before_params={},
            after_params={"signal_threshold": 0.6},
            metrics_window={},
            rationale="test promotion",
            status="pending",
            source="labs",
        )
        session.add(proposal)
        await session.flush()
        pid = proposal.id

    yield sid, pid

    async with shared_session_scope() as session:
        await session.execute(
            delete(MutationProposal).where(MutationProposal.strategy_id == sid)
        )
        await session.execute(
            delete(StrategyConfig).where(StrategyConfig.strategy_id == sid)
        )


@pytest.mark.asyncio
async def test_apply_proposal_creates_slot_config(strategy_and_proposal, test_wallet):
    sid, pid = strategy_and_proposal
    from labs.promote import apply_proposal

    ok = await apply_proposal(pid)
    assert ok is True

    async with shared_session_scope() as session:
        config = (await session.execute(
            select(StrategySlotConfig)
            .where(StrategySlotConfig.strategy_id == sid)
            .where(StrategySlotConfig.wallet_id == test_wallet)
        )).scalar_one_or_none()

    assert config is not None, "slot config should be created on first promotion"
    assert config.allocated_slots >= 1

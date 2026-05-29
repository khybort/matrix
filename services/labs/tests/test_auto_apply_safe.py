"""Tests for apply_best_pending_safe() eligibility filtering."""

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sid() -> str:
    return f"test_safe_{uuid.uuid4().hex[:8]}"


async def _insert_strategy(sid: str, asset_class: str = "crypto") -> None:
    async with shared_session_scope() as session:
        session.add(
            StrategyConfig(
                strategy_id=sid,
                asset_class=asset_class,
                version=1,
                status="active",
                params={"signal_threshold": 0.5},
                rationale="test",
            )
        )


async def _insert_proposal(
    sid: str,
    proposal_type: str,
    metrics_window: dict,
    source: str = "labs",
    asset_class: str = "crypto",
) -> uuid.UUID:
    async with shared_session_scope() as session:
        p = MutationProposal(
            strategy_id=sid,
            asset_class=asset_class,
            from_version=1,
            to_version=2,
            proposal_type=proposal_type,
            before_params={},
            after_params={"signal_threshold": 0.6},
            metrics_window=metrics_window,
            rationale="test",
            status="pending",
            source=source,
        )
        session.add(p)
        await session.flush()
        return p.id


async def _cleanup(sid: str) -> None:
    async with shared_session_scope() as session:
        await session.execute(
            delete(StrategySlotConfig).where(StrategySlotConfig.strategy_id == sid)
        )
        await session.execute(
            delete(MutationProposal).where(MutationProposal.strategy_id == sid)
        )
        await session.execute(
            delete(StrategyConfig).where(StrategyConfig.strategy_id == sid)
        )


async def _get_proposal_status(pid: uuid.UUID) -> str | None:
    async with shared_session_scope() as session:
        row = await session.get(MutationProposal, pid)
        return row.status if row else None


# ---------------------------------------------------------------------------
# Fixtures — wallet for slot config bootstrap
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def test_wallet():
    wid = uuid.uuid4()
    async with shared_session_scope() as session:
        session.add(
            Wallet(
                id=wid,
                name=f"test-safe-{wid}",
                asset_class="crypto",
                starting_capital_usd=Decimal("10000"),
                cash_usd=Decimal("10000"),
                locked_usd=Decimal("0"),
                max_position_pct=Decimal("0.02"),
                max_concurrent_positions=50,
                daily_loss_circuit_pct=Decimal("0.05"),
                day_start_equity=Decimal("10000"),
                day_start_at=datetime.now(timezone.utc),
            )
        )
    yield wid
    async with shared_session_scope() as session:
        await session.execute(
            delete(StrategySlotConfig).where(StrategySlotConfig.wallet_id == wid)
        )
        await session.execute(delete(Wallet).where(Wallet.id == wid))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_safe_apply_skips_low_fitness():
    """lab_promotion with fitness below threshold must stay pending."""
    from labs.promote import apply_best_pending_safe

    sid = _make_sid()
    await _insert_strategy(sid)
    pid = await _insert_proposal(
        sid,
        proposal_type="lab_promotion",
        metrics_window={"fitness_score": "0.05"},
    )

    try:
        applied = await apply_best_pending_safe(min_fitness=Decimal("0.10"))
        assert pid not in applied, "low-fitness proposal should not be applied"
        assert await _get_proposal_status(pid) == "pending"
    finally:
        await _cleanup(sid)


@pytest.mark.asyncio
async def test_safe_apply_takes_high_fitness(test_wallet):
    """lab_promotion with fitness >= threshold must be applied and create v2 config."""
    from labs.promote import apply_best_pending_safe

    sid = _make_sid()
    await _insert_strategy(sid)
    pid = await _insert_proposal(
        sid,
        proposal_type="lab_promotion",
        metrics_window={"fitness_score": "0.15"},
    )

    try:
        applied = await apply_best_pending_safe(min_fitness=Decimal("0.10"))
        assert pid in applied, "high-fitness proposal should be applied"
        assert await _get_proposal_status(pid) == "applied"

        # v2 config must exist and be active
        async with shared_session_scope() as session:
            cfg = (
                await session.execute(
                    select(StrategyConfig)
                    .where(StrategyConfig.strategy_id == sid)
                    .where(StrategyConfig.version == 2)
                )
            ).scalar_one_or_none()
        assert cfg is not None, "v2 StrategyConfig should exist"
        assert cfg.status == "active"
    finally:
        await _cleanup(sid)


@pytest.mark.asyncio
async def test_safe_apply_skips_weight_tune():
    """weight_tune proposals are not eligible regardless of metrics."""
    from labs.promote import apply_best_pending_safe

    sid = _make_sid()
    await _insert_strategy(sid)
    pid = await _insert_proposal(
        sid,
        proposal_type="weight_tune",
        metrics_window={"fitness_score": "0.99"},
        source="labs",
    )

    try:
        applied = await apply_best_pending_safe(min_fitness=Decimal("0.10"))
        assert pid not in applied, "weight_tune should not be eligible"
        assert await _get_proposal_status(pid) == "pending"
    finally:
        await _cleanup(sid)


@pytest.mark.asyncio
async def test_safe_apply_takes_slot_adjustment_high_losses(test_wallet):
    """slot_adjustment from slot_scorer with consecutive_losses >= 5 must be applied."""
    from labs.promote import apply_best_pending_safe

    sid = _make_sid()
    await _insert_strategy(sid)
    pid = await _insert_proposal(
        sid,
        proposal_type="slot_adjustment",
        metrics_window={"consecutive_losses": 5},
        source="slot_scorer",
    )

    try:
        applied = await apply_best_pending_safe(min_fitness=Decimal("0.10"))
        assert pid in applied, "slot_adjustment with 5+ losses should be applied"
        assert await _get_proposal_status(pid) == "applied"
    finally:
        await _cleanup(sid)

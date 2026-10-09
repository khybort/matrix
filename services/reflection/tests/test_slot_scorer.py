"""Unit + integration tests for the slot scorer."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

import reflection.slot_scorer as _ss
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


def test_perf_score_neutral_is_half():
    """A coin-flip win rate with flat PnL must land at the 0.5 neutral point —
    the value edge_multiplier / risk_multiplier read as neutral 1.0x. Regression
    guard for the signed-scale bug that penalised every scored strategy."""
    import pytest as _pytest
    from reflection.slot_scorer import _perf_score
    assert _perf_score(win_rate=0.5, avg_pnl_pct=0.0, total_pnl_usd=0.0) == _pytest.approx(0.5)


def test_perf_score_stays_in_unit_interval():
    from reflection.slot_scorer import _perf_score
    for wr in (0.0, 0.5, 1.0):
        for pct in (-0.1, 0.0, 0.1):
            for pnl in (-100.0, 0.0, 100.0):
                s = _perf_score(wr, pct, pnl)
                assert 0.0 <= s <= 1.0


def test_perf_score_rewards_high_total_pnl_with_small_edge():
    """Small per-trade edge but consistently positive total — should NOT score below 0.5.
    This is the funding_reversion bug: high win rate, small avg_pnl_pct, positive total."""
    from reflection.slot_scorer import _perf_score
    # 53% win, 0.025% avg pnl, +$10 total over 30 trades
    score = _perf_score(win_rate=0.53, avg_pnl_pct=0.00025, total_pnl_usd=10.0)
    assert score >= 0.5, f"expected >=0.5, got {score}"


def test_auto_cut_caps_but_never_grants_a_slot():
    from reflection.slot_scorer import _auto_cut_slots
    assert _auto_cut_slots(6) == 1
    assert _auto_cut_slots(1) == 1
    assert _auto_cut_slots(0) == 0


def test_re_entered_fills_of_one_bet_score_as_one_loss():
    """One wrong call held three times read as three consecutive losses. It is
    one bet with the summed dollars."""
    from types import SimpleNamespace

    from reflection.slot_scorer import _bets

    t0 = datetime(2026, 9, 20, tzinfo=timezone.utc)

    def fill(minute, pnl, symbol="AAAUSDT"):
        pos = SimpleNamespace(asset_class="crypto", symbol=symbol, side="long", pnl_usd=Decimal(str(pnl)),
                              notional_usd=Decimal("100"), closed_at=t0 + timedelta(minutes=minute + 60))
        return (pos, t0 + timedelta(minutes=minute), 3600)

    bets = _bets([fill(0, -1), fill(5, -1), fill(10, -1), fill(2, 3, symbol="BBBUSDT"), fill(90, 1)])
    # oldest first by close: BBB (closed +62), the AAA episode (+70), the late AAA (+150)
    assert [round(b.pnl_usd, 6) for b in bets] == [3.0, -3.0, 1.0]
    assert bets[1].notional_usd == 300.0


def test_a_strategy_that_is_not_running_is_never_promoted():
    """momentum_xs had no active config after 09-21 and still logged
    "slot promote 2→8" every ~11 min. Not running: hold or fall, never rise."""
    from reflection.slot_scorer import _cap_promotion

    assert _cap_promotion(2, 8, live=False) == 2
    assert _cap_promotion(2, 8, live=True) == 8
    assert _cap_promotion(8, 2, live=False) == 2      # demotion still applies


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


@pytest.fixture(autouse=True)
def _small_evidence_gate(monkeypatch):
    """Legacy fixtures seed a handful of positions; the production gate is 30."""
    monkeypatch.setattr(_ss, "MIN_N_FOR_SLOT_CHANGE", 1)


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
        # positions → predictions → wallet: the seeded rows used to outlive the
        # test (342 TEST_scorer_* predictions were found in the live table).
        await session.execute(delete(PaperPosition).where(PaperPosition.wallet_id == wid))
        await session.execute(delete(Prediction).where(Prediction.strategy_id == STRAT))
        await session.execute(delete(Wallet).where(Wallet.id == wid))


async def _seed_closed_position(
    wallet_id: uuid.UUID, pnl_usd: float, notional: float = 200.0, symbol: str | None = None
) -> None:
    pred_id = uuid.uuid4()
    # A distinct symbol per seed makes each position its own bet; the scorer
    # counts episodes, and same-(symbol, side) signals inside one horizon are one.
    symbol = symbol or f"TEST_{pred_id.hex[:10]}"
    # Two-session insert: prediction first (FK target), position after.
    now = datetime.now(timezone.utc)
    async with shared_session_scope() as session:
        session.add(Prediction(
            id=pred_id,
            strategy_id=STRAT,
            strategy_version=1,
            asset_class=ASSET,
            symbol=symbol,
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
            symbol=symbol,
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
async def test_realized_loser_demoted_to_zero_slots(scorer_wallet):
    """A strategy net-negative over a full window is pulled from the active book
    entirely (allocated_slots → 0), not just trimmed to 1. A recent win breaks
    the consecutive-loss streak so this exercises the realized-loss demote path,
    not the consec-auto-cut. Backstops the EV floor for confidently-wrong
    strategies whose modeled EV clears cost while realized edge is negative."""
    for _ in range(22):
        await _seed_closed_position(scorer_wallet, pnl_usd=-2.0)
    for _ in range(3):  # most-recent wins → consec streak < 8, forces score path
        await _seed_closed_position(scorer_wallet, pnl_usd=0.5)

    from reflection.slot_scorer import score_strategy_slots
    await score_strategy_slots()

    async with shared_session_scope() as session:
        cfg = await session.get(StrategySlotConfig, (STRAT, ASSET, scorer_wallet))
    assert cfg.allocated_slots == 0, (
        f"realized loser should be demoted to 0 slots, got {cfg.allocated_slots}"
    )


@pytest.mark.asyncio
async def test_high_performance_increases_slots(scorer_wallet, monkeypatch):
    """High win rate + positive pnl → slots should increase above initial."""
    async def running(*a, **k):
        return True

    # The fixture strategy has no config row; promotion requires a running one.
    monkeypatch.setattr(_ss, "_is_live", running)
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


def test_wilson_lower_is_conservative_for_small_n():
    from reflection.slot_scorer import wilson_lower
    assert wilson_lower(7, 10) < 0.7 and wilson_lower(7, 10) > 0.35
    assert wilson_lower(70, 100) > wilson_lower(7, 10)
    assert wilson_lower(0, 0) == 0.0 and wilson_lower(0, 10) == 0.0


@pytest.mark.asyncio
async def test_shadow_wallet_slot_rows_are_not_scored():
    """Challenger wallet rows must be left untouched (the engine borrows the
    champion's slots for the shadow pass)."""
    from reflection.slot_scorer import score_strategy_slots
    wid = uuid.uuid4()
    strat = f"TEST_shadowslot_{uuid.uuid4().hex[:6]}"
    async with shared_session_scope() as session:
        session.add(Wallet(id=wid, name="shadow", asset_class="test", starting_capital_usd=Decimal("10000"),
                           cash_usd=Decimal("10000"), locked_usd=Decimal("0"), max_position_pct=Decimal("0.05"),
                           max_concurrent_positions=50, daily_loss_circuit_pct=Decimal("0.10"),
                           day_start_equity=Decimal("10000"), day_start_at=datetime.now(timezone.utc)))
    async with shared_session_scope() as session:
        session.add(StrategySlotConfig(strategy_id=strat, asset_class="test", wallet_id=wid,
                                       allocated_slots=10, perf_score=0.5, consecutive_losses=0))
    try:
        for pnl in (-1.0,) * 9:
            await _seed_closed_position(wid, pnl)
        await score_strategy_slots()
        async with shared_session_scope() as session:
            row = await session.get(StrategySlotConfig, (strat, "test", wid))
            assert row.allocated_slots == 10 and row.last_evaluated_at is None
    finally:
        async with shared_session_scope() as session:
            pids = list((await session.execute(
                select(PaperPosition.prediction_id).where(PaperPosition.wallet_id == wid))).scalars())
            await session.execute(delete(PaperPosition).where(PaperPosition.wallet_id == wid))
            if pids:
                await session.execute(delete(Prediction).where(Prediction.id.in_(pids)))
            await session.execute(delete(StrategySlotConfig).where(StrategySlotConfig.wallet_id == wid))
            await session.execute(delete(Wallet).where(Wallet.id == wid))


@pytest.mark.asyncio
async def test_a_strategy_with_measured_edge_gets_at_least_a_full_share(monkeypatch):
    """Trailing PnL is a rearview mirror: momentum_xs sat at the 1-slot floor
    while the controlled study showed its entries beating both nulls."""
    import reflection.slot_scorer as SS

    seen = {}

    async def fake_verdict(sid, ac):
        seen["called"] = (sid, ac)
        return "pays"

    monkeypatch.setattr(SS, "_entry_edge_verdict", fake_verdict)
    # a weak score would normally land on base_share // 4
    weak = SS._slots_for_score(0.20, base_share=8)
    assert weak == 2
    # the promotion lifts it to the full share; the helper is what the pass uses
    assert max(weak, 8) == 8
    assert await SS._entry_edge_verdict("momentum_xs", "crypto") == "pays"
    assert seen["called"] == ("momentum_xs", "crypto")


async def test_unknown_edge_holds_an_allocation_instead_of_clawing_it_back(monkeypatch):
    """A restart empties the edge cache, so `strategy_edge` answers None for a
    while. On 2026-09-20 that took momentum_xs from 7 slots to 3 seconds after
    the previous pass had promoted it on a confirmed +29.7 bps edge. An
    allocation granted on evidence is not reduced because the evidence is
    momentarily unreadable — but a realised loser is still demoted, because
    that branch stands on evidence of its own and the edge verdict may only
    rescue it, never be missing in its favour."""
    async def no_answer(strategy_id, asset_class, **kw):
        return None

    import matrix_shared.edge_study as E

    monkeypatch.setattr(E, "strategy_edge", no_answer)
    v = await _ss._entry_edge_verdict("anything", "crypto")
    assert v == "unknown"

"""Unit tests for the AI Strategy Suggester.

Pure-Python where possible; the only async bit is the suggest() orchestrator
which we cover with a monkeypatched run_backtest so tests stay DB-free and
under 0.5s.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from backtest.historical import BacktestResult
from backtest.suggester import (
    Candidate,
    RISK_WEIGHTS,
    STRATEGY_PARAM_GRIDS,
    classify,
    enumerate_params,
    score,
    suggest,
)


# ----------------------------------------------------------------- score


def _result(
    *, pnl: str = "10.0", win_rate: str = "0.55", dd: str = "0.05", n_pos: int = 20
) -> BacktestResult:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return BacktestResult(
        strategy_id="grid",
        params={},
        window=(now, now),
        n_bars=1500,
        n_predictions=n_pos,
        n_positions_opened=n_pos,
        n_positions_closed=n_pos,
        avg_pnl_usd=Decimal(pnl) / Decimal(max(n_pos, 1)),
        total_pnl_usd=Decimal(pnl),
        win_rate=Decimal(win_rate),
        max_drawdown_pct=Decimal(dd),
    )


def test_score_conservative_penalizes_drawdown():
    """Same pnl + win_rate, higher dd → lower conservative score."""
    low_dd = _result(pnl="10", win_rate="0.6", dd="0.01")
    high_dd = _result(pnl="10", win_rate="0.6", dd="0.10")
    w = RISK_WEIGHTS["conservative"]
    assert score(low_dd, w) > score(high_dd, w)


def test_score_aggressive_rewards_pnl_more_than_conservative():
    """Aggressive should rank a high-pnl, high-dd config above the conservative ranking."""
    big_winner = _result(pnl="100", win_rate="0.5", dd="0.20")
    small_safe = _result(pnl="5", win_rate="0.7", dd="0.02")
    agg = RISK_WEIGHTS["aggressive"]
    cons = RISK_WEIGHTS["conservative"]
    # Aggressive: big_winner > small_safe
    assert score(big_winner, agg) > score(small_safe, agg)
    # Conservative: order should flip
    assert score(small_safe, cons) > score(big_winner, cons)


# ----------------------------------------------------------------- classify


def test_classify_underperforming_when_pnl_negative():
    r = _result(pnl="-5", dd="0.02")
    pool = [r, _result(pnl="10"), _result(pnl="20")]
    assert classify(r, all_results=pool) == "underperforming"


def test_classify_high_yield_at_top_pnl():
    top = _result(pnl="50", win_rate="0.6", dd="0.1")
    pool = [_result(pnl=str(i), win_rate="0.5", dd="0.05") for i in range(1, 10)] + [top]
    assert classify(top, all_results=pool) == "high_yield"


def test_classify_stable_at_low_drawdown():
    safe = _result(pnl="3", win_rate="0.55", dd="0.001")
    pool = [_result(pnl="3", win_rate="0.55", dd=str(d / 100)) for d in range(1, 10)] + [safe]
    assert classify(safe, all_results=pool) == "stable"


# ----------------------------------------------------------------- sampling


def test_enumerate_full_product():
    combos = enumerate_params({"a": [1, 2], "b": ["x", "y"]})
    assert len(combos) == 4
    assert {"a": 1, "b": "x"} in combos
    assert {"a": 2, "b": "y"} in combos


def test_enumerate_sample_seeded_deterministic():
    grid = {"a": [1, 2, 3, 4], "b": ["x", "y"]}
    s1 = enumerate_params(grid, max_combinations=3, seed=42)
    s2 = enumerate_params(grid, max_combinations=3, seed=42)
    assert s1 == s2
    assert len(s1) == 3


def test_default_grid_strategy_grid_present():
    """Smoke: the strategy registry exposes 'grid' with at least one knob."""
    assert "grid" in STRATEGY_PARAM_GRIDS
    assert "n_grids" in STRATEGY_PARAM_GRIDS["grid"]


# ----------------------------------------------------------------- orchestrator


@pytest.mark.asyncio
async def test_suggest_picks_top_k_by_score(monkeypatch):
    """suggest() should call run_backtest per combination, rank by score,
    and return only top_k. Monkeypatch run_backtest to a deterministic
    fake so we don't hit the DB."""
    # Tiny grid so the test is fast: 2 * 2 = 4 combos.
    grid = {"n_grids": [5, 10], "price_band_pct": [Decimal("0.01"), Decimal("0.02")]}

    # Fake result: pnl varies linearly with n_grids so we know who wins.
    async def _fake_run_backtest(strategy, symbol, asset_class, days, params):
        n = int(params["n_grids"])
        return BacktestResult(
            strategy_id=strategy, params=params,
            window=(datetime(2026, 1, 1, tzinfo=timezone.utc),
                    datetime(2026, 1, 2, tzinfo=timezone.utc)),
            n_bars=1440, n_predictions=n, n_positions_opened=n, n_positions_closed=n,
            avg_pnl_usd=Decimal("1.0"), total_pnl_usd=Decimal(n),
            win_rate=Decimal("0.6"), max_drawdown_pct=Decimal("0.05"),
        )
    monkeypatch.setattr("backtest.suggester.run_backtest", _fake_run_backtest)

    run = await suggest(
        strategy="grid", symbol="BTCUSDT", days=1,
        risk_profile="balanced", top_k=2, grids=grid,
    )
    assert run.n_samples == 4
    assert len(run.candidates) == 2
    # Best should be n_grids=10 (higher pnl beats higher conservatism here)
    assert run.candidates[0].params["n_grids"] == 10
    # Score monotonic across candidates
    assert run.candidates[0].score >= run.candidates[1].score


@pytest.mark.asyncio
async def test_suggest_skips_failures(monkeypatch):
    """If run_backtest raises, the suggester logs and continues."""
    grid = {"n_grids": [5, 10]}
    calls = {"n": 0}

    async def _fake(strategy, symbol, asset_class, days, params):
        calls["n"] += 1
        if params["n_grids"] == 5:
            raise RuntimeError("simulated backtest failure")
        return BacktestResult(
            strategy_id=strategy, params=params,
            window=(datetime(2026, 1, 1, tzinfo=timezone.utc),
                    datetime(2026, 1, 2, tzinfo=timezone.utc)),
            n_bars=1440, n_predictions=1, n_positions_opened=1, n_positions_closed=1,
            avg_pnl_usd=Decimal("1"), total_pnl_usd=Decimal("1"),
            win_rate=Decimal("1"), max_drawdown_pct=Decimal("0"),
        )
    monkeypatch.setattr("backtest.suggester.run_backtest", _fake)

    run = await suggest(
        strategy="grid", symbol="BTCUSDT", days=1, top_k=5, grids=grid,
    )
    assert calls["n"] == 2
    assert len(run.candidates) == 1  # only n_grids=10 survived
    assert run.candidates[0].params["n_grids"] == 10


@pytest.mark.asyncio
async def test_suggest_rationale_skipped_when_llm_disabled(monkeypatch):
    """with_rationale=True but LLM disabled → candidates.rationale stays None."""
    grid = {"n_grids": [5]}

    async def _fake(strategy, symbol, asset_class, days, params):
        return BacktestResult(
            strategy_id=strategy, params=params,
            window=(datetime(2026, 1, 1, tzinfo=timezone.utc),
                    datetime(2026, 1, 2, tzinfo=timezone.utc)),
            n_bars=10, n_predictions=1, n_positions_opened=1, n_positions_closed=1,
            avg_pnl_usd=Decimal("1"), total_pnl_usd=Decimal("1"),
            win_rate=Decimal("1"), max_drawdown_pct=Decimal("0"),
        )
    monkeypatch.setattr("backtest.suggester.run_backtest", _fake)
    # Force llm_enabled() to return False by stubbing import
    import matrix_shared
    monkeypatch.setattr(matrix_shared, "llm_enabled", lambda: False)

    run = await suggest(
        strategy="grid", symbol="BTCUSDT", days=1, top_k=1, grids=grid,
        with_rationale=True,
    )
    assert run.candidates[0].rationale is None

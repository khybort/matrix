"""Unit + smoke tests for the historical backtest engine.

Pure-Python tests use synthetic MarketBar lists (no DB).
The final smoke test queries the live matrix-postgres container — gated
behind BACKTEST_LIVE_DB env var so CI without a running stack stays green.
"""

from __future__ import annotations

import os
from decimal import Decimal

import pytest

from backtest.historical import (
    BacktestResult,
    _grid_lines,
    _max_drawdown_pct,
    grid_replay,
    run_backtest,
)


# ----------------------------------------------------------------- helpers


def _params(n_grids: int = 10, band_pct: str = "0.02", horizon_s: int = 300) -> dict:
    return {
        "n_grids": n_grids,
        "price_band_pct": Decimal(band_pct),
        "horizon_s": horizon_s,
    }


# ----------------------------------------------------------------- unit tests


def test_grid_lines_evenly_spaced():
    lines = _grid_lines(Decimal("100"), Decimal("0.1"), 5)
    assert len(lines) == 5
    assert lines[0] == Decimal("90")
    assert lines[-1] == Decimal("110")
    # Evenly spaced
    diffs = [lines[i + 1] - lines[i] for i in range(len(lines) - 1)]
    assert all(d == diffs[0] for d in diffs)


def test_max_drawdown_pct():
    curve = [Decimal("100"), Decimal("120"), Decimal("90"), Decimal("110")]
    # Peak 120 → trough 90 = 25% drawdown
    dd = _max_drawdown_pct(curve)
    assert dd == Decimal("0.25").quantize(Decimal("0.000001"))


def test_max_drawdown_pct_monotonic_no_drawdown():
    curve = [Decimal("100"), Decimal("110"), Decimal("120")]
    assert _max_drawdown_pct(curve) == Decimal("0")


def test_grid_replay_with_flat_bars_emits_no_trades(flat_bars):
    result = grid_replay(flat_bars, _params())
    assert isinstance(result, BacktestResult)
    assert result.strategy_id == "grid"
    assert result.n_bars == len(flat_bars)
    assert result.n_predictions == 0
    assert result.n_positions_opened == 0
    assert result.total_pnl_usd == Decimal("0")


def test_grid_replay_emits_on_crossover(swinging_bars):
    result = grid_replay(swinging_bars, _params())
    assert result.n_predictions >= 1
    assert result.n_positions_opened >= 1
    # Each opened position must have been closed (force-close at last bar)
    assert result.n_positions_closed == result.n_positions_opened
    for p in result.positions:
        assert p.closed_ts is not None
        assert p.closed_price is not None


def test_grid_replay_pnl_signs_make_sense(dip_then_uptrend_bars):
    """Dip-buy long that recovers above entry → positive pnl (modulo slippage)."""
    result = grid_replay(dip_then_uptrend_bars, _params())
    assert result.n_positions_opened >= 1
    # Aggregate pnl should be positive — recovery > 2 bps slippage cost
    assert result.total_pnl_usd > 0, (
        f"expected positive PnL on dip-buy uptrend, got {result.total_pnl_usd}"
    )
    # win rate should also be > 0.5 on this construction
    assert result.win_rate > Decimal("0.5")


def test_grid_replay_empty_bars():
    result = grid_replay([], _params())
    assert result.n_bars == 0
    assert result.n_positions_opened == 0
    assert result.total_pnl_usd == Decimal("0")


def test_grid_replay_respects_notional_sizing(swinging_bars):
    """notional_usd per position must equal starting_capital * max_position_pct."""
    result = grid_replay(
        swinging_bars,
        _params(),
        starting_capital=Decimal("10000"),
        max_position_pct=Decimal("0.02"),
    )
    assert result.n_positions_opened >= 1
    for p in result.positions:
        assert p.notional_usd == Decimal("200.00")


def test_grid_replay_horizon_aligned_to_bars():
    """horizon_s=120 should close positions 2 bars after open on 1m bars."""
    from datetime import UTC, datetime, timedelta

    from tests.historical.conftest import make_bar

    base = datetime(2026, 1, 1, tzinfo=UTC)
    # 1500 flat warmup bars then one dip that fires an entry
    bars = [make_bar(ts=base + timedelta(minutes=i), close=Decimal("50000"))
            for i in range(1500)]
    # crossover-triggering dip
    bars.append(make_bar(ts=base + timedelta(minutes=1500), close=Decimal("49500")))
    # two follow-up bars so horizon can elapse cleanly
    bars.append(make_bar(ts=base + timedelta(minutes=1501), close=Decimal("49500")))
    bars.append(make_bar(ts=base + timedelta(minutes=1502), close=Decimal("49500")))
    bars.append(make_bar(ts=base + timedelta(minutes=1503), close=Decimal("49500")))

    result = grid_replay(bars, _params(horizon_s=120))
    assert result.n_positions_opened >= 1
    # 120s horizon = 2 bars → exit_ts = open_ts + ~2min
    p = result.positions[0]
    elapsed = (p.closed_ts - p.opened_ts).total_seconds()
    # Allowing 1 bar fuzz because exit fires on the first bar where i >= exit_idx
    assert 60 <= elapsed <= 240, f"horizon mis-aligned: elapsed={elapsed}s"


# ----------------------------------------------------------------- smoke test


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get("BACKTEST_LIVE_DB"),
    reason="set BACKTEST_LIVE_DB=1 to run against the live matrix-postgres bars",
)
async def test_run_backtest_grid_on_live_data():
    """Smoke: run end-to-end against whatever's in market_bars right now.

    Does NOT assert PnL signs — just verifies the pipeline works and reads
    a non-trivial number of bars."""
    result = await run_backtest(
        "grid",
        "BTCUSDT",
        "crypto",
        days=1,
        params=_params(),
    )
    assert isinstance(result, BacktestResult)
    assert result.strategy_id == "grid"
    assert result.n_bars > 0


def test_run_backtest_rejects_unknown_strategy():
    import asyncio

    with pytest.raises(ValueError, match="unknown strategy"):
        asyncio.run(run_backtest("tradeflow", "BTCUSDT", "crypto", 1, None))

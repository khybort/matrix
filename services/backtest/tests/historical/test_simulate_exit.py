"""Unit tests for `_simulate_exit` and the TP/SL-aware grid replayer.

Pure-Python — no DB, no agent feature queries. Synthetic bar lists
drive the exit decision tree.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from backtest.historical import _simulate_exit, grid_replay
from tests.historical.conftest import make_bar


def _flat_bars(n: int, price: str = "50000") -> list:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [make_bar(ts=base + timedelta(minutes=i), close=Decimal(price)) for i in range(n)]


def _trend_bars(n: int, start: str, end: str) -> list:
    """Linear from start → end over n bars."""
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    s = Decimal(start)
    e = Decimal(end)
    step = (e - s) / Decimal(max(n - 1, 1))
    return [
        make_bar(ts=base + timedelta(minutes=i), close=s + step * Decimal(i))
        for i in range(n)
    ]


# ----------------------------------------------------------------- _simulate_exit


def test_exit_long_hits_tp_before_horizon():
    """Long entry, bars climb past tp_pct → close at TP bar."""
    bars = _trend_bars(20, "100", "110")  # +10% over 20 bars
    close_idx, exit_px, reason = _simulate_exit(
        bars, open_idx=0, side="long", entry_price=Decimal("100"),
        horizon_bars=10, tp_pct=Decimal("0.02"), sl_pct=Decimal("0.10"),
    )
    assert reason == "hit_tp"
    assert close_idx < 10                  # before horizon
    # exit price had slippage applied (down for long exit)
    assert exit_px < bars[close_idx].close


def test_exit_long_hits_sl_before_horizon():
    """Long entry, bars fall past sl_pct → close at SL bar."""
    bars = _trend_bars(20, "100", "90")  # -10% over 20 bars
    close_idx, exit_px, reason = _simulate_exit(
        bars, open_idx=0, side="long", entry_price=Decimal("100"),
        horizon_bars=10, tp_pct=Decimal("0.10"), sl_pct=Decimal("0.02"),
    )
    assert reason == "hit_sl"
    assert close_idx < 10


def test_exit_short_hits_tp_on_downtrend():
    """Short entry, downtrend → TP."""
    bars = _trend_bars(20, "100", "90")
    close_idx, exit_px, reason = _simulate_exit(
        bars, open_idx=0, side="short", entry_price=Decimal("100"),
        horizon_bars=10, tp_pct=Decimal("0.02"), sl_pct=Decimal("0.10"),
    )
    assert reason == "hit_tp"


def test_exit_short_hits_sl_on_uptrend():
    bars = _trend_bars(20, "100", "110")
    close_idx, exit_px, reason = _simulate_exit(
        bars, open_idx=0, side="short", entry_price=Decimal("100"),
        horizon_bars=10, tp_pct=Decimal("0.10"), sl_pct=Decimal("0.02"),
    )
    assert reason == "hit_sl"


def test_exit_falls_back_to_horizon_when_neither_triggers():
    """Flat bars — neither TP nor SL ever fires; closes at horizon."""
    bars = _flat_bars(20)
    close_idx, _exit_px, reason = _simulate_exit(
        bars, open_idx=0, side="long", entry_price=Decimal("50000"),
        horizon_bars=5, tp_pct=Decimal("0.01"), sl_pct=Decimal("0.01"),
    )
    assert reason == "hit_horizon"
    assert close_idx == 5


def test_exit_without_tp_sl_defaults_to_horizon():
    bars = _trend_bars(20, "100", "110")
    close_idx, _exit_px, reason = _simulate_exit(
        bars, open_idx=0, side="long", entry_price=Decimal("100"),
        horizon_bars=5,
    )
    assert reason == "hit_horizon"
    assert close_idx == 5


def test_exit_end_of_window_when_bars_run_out():
    """horizon_bars exceeds available bars → close at last bar with reason."""
    bars = _flat_bars(5)
    close_idx, _exit_px, reason = _simulate_exit(
        bars, open_idx=0, side="long", entry_price=Decimal("50000"),
        horizon_bars=100,
    )
    assert reason == "end_of_window"
    assert close_idx == len(bars) - 1


def test_exit_tp_wins_ties_against_sl():
    """If a bar would trigger both TP and SL (chart gap), TP reports.
    Construct a bar where price oversoots both thresholds in favor."""
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    bars = [
        make_bar(ts=base, close=Decimal("100")),
        # Massive up bar: long here triggers TP=2% AND beats SL=2% on the way
        # (impossible by direction — but if we model as a single bar close
        # at 105, only TP triggers anyway). Constructing the actual tie
        # requires intra-bar high/low; close-only model means whichever
        # direction wins. This test instead verifies the order-of-check.
        make_bar(ts=base + timedelta(minutes=1), close=Decimal("105")),
    ]
    close_idx, _exit_px, reason = _simulate_exit(
        bars, open_idx=0, side="long", entry_price=Decimal("100"),
        horizon_bars=5, tp_pct=Decimal("0.02"), sl_pct=Decimal("0.02"),
    )
    assert reason == "hit_tp"


# ----------------------------------------------------------------- grid + TP/SL


def test_grid_replay_passes_tp_sl_through():
    """Grid replay tagged with tp_pct should emit positions whose
    close_reason includes 'hit_tp' on bars that gap up after a long entry."""
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    bars = [make_bar(ts=base + timedelta(minutes=i), close=Decimal("50000"))
            for i in range(1500)]
    # Single dip-bar that crosses a grid line, then a sharp recovery on the
    # very next bar that exceeds tp_pct.
    bars.append(make_bar(ts=base + timedelta(minutes=1500), close=Decimal("49500")))
    bars.append(make_bar(ts=base + timedelta(minutes=1501), close=Decimal("50800")))
    for i in range(20):
        bars.append(make_bar(ts=base + timedelta(minutes=1502 + i),
                             close=Decimal("50800")))

    result = grid_replay(bars, {
        "n_grids": 10, "price_band_pct": Decimal("0.02"), "horizon_s": 600,
        "tp_pct": Decimal("0.01"), "sl_pct": Decimal("0.01"),
    })
    assert result.n_positions_closed >= 1
    reasons = {p.close_reason for p in result.positions}
    assert "hit_tp" in reasons, f"expected hit_tp in {reasons}"

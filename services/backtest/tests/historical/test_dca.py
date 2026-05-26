"""Synthetic-bar tests for the DCA replayer."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from backtest.historical import SLIPPAGE_BPS
from backtest.replayers.dca import dca_replay
from tests.historical.conftest import make_bar


def _flat_bars(price: str = "50000", n: int = 1440) -> list:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [make_bar(ts=base + timedelta(minutes=i), close=Decimal(price)) for i in range(n)]


def _uptrend_bars(start: str = "50000", end: str = "55000", n: int = 1440) -> list:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    s = Decimal(start)
    delta = (Decimal(end) - s) / Decimal(max(n - 1, 1))
    return [
        make_bar(ts=base + timedelta(minutes=i), close=s + delta * Decimal(i))
        for i in range(n)
    ]


def test_dca_empty_bars():
    result = dca_replay([], {"interval_minutes": 60})
    assert result.n_positions_closed == 0
    assert result.total_pnl_usd == Decimal("0")
    assert result.strategy_id == "dca"


def test_dca_flat_bars_realizes_round_trip_slippage():
    """Constant-price world: every buy closes at same price minus 2*bps
    slippage. PnL should be uniformly small-negative."""
    bars = _flat_bars(price="50000", n=600)
    result = dca_replay(bars, {"interval_minutes": 60})
    # 600 bars / 60-min cadence = 10 buys
    assert result.n_positions_closed == 10
    # Each buy pays open-bps + close-bps = ~4 bps round-trip → negative pnl
    for p in result.positions:
        assert p.pnl_usd < Decimal("0")
    assert result.win_rate == Decimal("0")


def test_dca_uptrend_profits():
    """Strictly uptrending bars: every DCA buy is cheaper than the final
    mark, so PnL is positive net of round-trip slippage."""
    bars = _uptrend_bars(start="50000", end="55000", n=1440)
    result = dca_replay(bars, {"interval_minutes": 60})
    # 1440 / 60 = 24 buys
    assert result.n_positions_closed == 24
    assert result.total_pnl_usd > Decimal("0")
    assert result.win_rate == Decimal("1")  # every buy wins on a steady uptrend


def test_dca_cadence_changes_position_count():
    """Tighter cadence (15min) on the same bars → more positions."""
    bars = _flat_bars(price="50000", n=300)
    r_short = dca_replay(bars, {"interval_minutes": 15})
    r_long = dca_replay(bars, {"interval_minutes": 60})
    assert r_short.n_positions_closed > r_long.n_positions_closed
    assert r_short.n_positions_closed == 20    # 300 / 15
    assert r_long.n_positions_closed == 5      # 300 / 60


def test_dca_slippage_constant_matches_historical_default():
    """Sanity: dca_replay imports SLIPPAGE_BPS from historical so we
    catch drift in either direction."""
    assert SLIPPAGE_BPS == Decimal("2")

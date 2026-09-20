"""A prediction filled late is a different trade from the one the strategy
proposed. Measured 2026-09-20: fills landed 20–53% into the horizon and turned
a +36 bps signal (momentum_xs) into a −36 bps entry."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from backtest.paper_trade import (
    MAX_SIGNAL_AGE_FRAC,
    MIN_SIGNAL_WINDOW_S,
    is_fresh_enough,
    signal_age_frac,
)

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


def _born(seconds_ago: float) -> datetime:
    return NOW - timedelta(seconds=seconds_ago)


def test_age_fraction_is_elapsed_over_horizon():
    assert signal_age_frac(_born(0), 600, NOW) == 0.0
    assert signal_age_frac(_born(300), 600, NOW) == 0.5
    assert signal_age_frac(_born(1200), 600, NOW) == 2.0
    assert signal_age_frac(_born(300), 0, NOW) == 0.0      # no horizon → no decay
    assert signal_age_frac(NOW + timedelta(seconds=5), 600, NOW) == 0.0   # clock skew


def test_fresh_window_is_a_fraction_of_the_horizon():
    assert MAX_SIGNAL_AGE_FRAC <= 0.5
    long_horizon = 3600
    assert is_fresh_enough(_born(60), long_horizon, NOW)
    # 53% into a 1h horizon — the momentum_xs average — must now be refused
    assert not is_fresh_enough(_born(1919), long_horizon, NOW)
    assert not is_fresh_enough(_born(long_horizon * MAX_SIGNAL_AGE_FRAC + 60), long_horizon, NOW)


def test_short_horizons_keep_an_absolute_window():
    # 20% of a 60s horizon would be 12s, too tight to ever fill
    assert is_fresh_enough(_born(MIN_SIGNAL_WINDOW_S - 1), 60, NOW)
    assert not is_fresh_enough(_born(MIN_SIGNAL_WINDOW_S + 30), 60, NOW)


def test_missing_horizon_never_blocks():
    assert is_fresh_enough(_born(99999), None, NOW)
    assert is_fresh_enough(_born(99999), 0, NOW)

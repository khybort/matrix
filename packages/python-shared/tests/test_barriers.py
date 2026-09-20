"""Volatility-scaled barriers: the distance must track σ and stay sane."""

from __future__ import annotations

import math

from matrix_shared.barriers import MAX_PCT, MIN_PCT, scale


def test_distance_scales_with_sigma_and_root_time():
    # 1-minute σ of 0.1%, 100-minute horizon → 1% at m=1
    assert math.isclose(scale(0.001, 6000, m=1.0), 0.01, rel_tol=1e-6)
    # four times the horizon doubles the distance
    assert math.isclose(scale(0.001, 24000, m=1.0), 0.02, rel_tol=1e-6)
    # double the multiple doubles the distance
    assert math.isclose(scale(0.001, 6000, m=2.0), 0.02, rel_tol=1e-6)


def test_quiet_and_violent_regimes_move_the_barrier():
    quiet = scale(0.0002, 3600, m=1.5)
    wild = scale(0.004, 3600, m=1.5)
    assert quiet < wild
    assert quiet >= MIN_PCT and wild <= MAX_PCT


def test_clamped_so_a_barrier_is_never_inside_the_spread_or_absurd():
    assert scale(1e-9, 600, m=1.0) == MIN_PCT
    assert scale(0.5, 600, m=3.0) == MAX_PCT


def test_unknown_volatility_returns_none_so_the_caller_keeps_its_own():
    assert scale(0.0, 600) is None
    assert scale(-1.0, 600) is None
    assert scale(float("nan"), 600) is None


def test_missing_horizon_floors_at_one_bar():
    assert scale(0.001, 0, m=1.0) == MIN_PCT or scale(0.001, 0, m=1.0) == 0.001
    assert scale(0.001, None, m=1.0) is not None

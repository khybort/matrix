"""Unit tests for matrix_shared.allocation."""

from __future__ import annotations

from decimal import Decimal

from matrix_shared import allocation as alloc
from matrix_shared.allocation import (
    edge_multiplier,
    expected_value,
    risk_multiplier,
    shrink_pair_edge,
)


def test_shrink_pair_edge_neutral_without_data():
    assert shrink_pair_edge(0, 0.0) == 0.5


def test_shrink_pair_edge_rewards_positive_returns():
    strong = shrink_pair_edge(20, 0.04)
    weak = shrink_pair_edge(20, -0.04)
    assert strong > 0.5 > weak


def test_edge_multiplier_bounds():
    assert edge_multiplier(None) == 1.0
    assert edge_multiplier(0.0) == 0.5
    assert edge_multiplier(1.0) == 1.5


def test_expected_value_prefers_good_pairing():
    base = expected_value(confidence=0.6, tp_pct=0.03, sl_pct=0.015)
    boosted = expected_value(
        confidence=0.6,
        tp_pct=0.03,
        sl_pct=0.015,
        pair_edge=0.9,
        strategy_perf=0.8,
    )
    assert boosted > base


def test_risk_multiplier_scales_down_on_loss_streak():
    calm = risk_multiplier(confidence=Decimal("0.8"), perf_score=0.7, pair_edge=0.7)
    hot = risk_multiplier(
        confidence=Decimal("0.8"),
        perf_score=0.7,
        pair_edge=0.7,
        consecutive_losses=8,
    )
    assert hot < calm
    assert hot >= Decimal("0.05")


# --- Kelly sizing on a measured edge ------------------------------------------


def test_kelly_sizes_below_the_gate_for_a_real_but_noisy_edge():
    """momentum_xs's live shape: +31 bps over the random-entry null at t=5.25,
    per-trade sd 120 bps, 80 concurrent slots. Kelly must land *under* the 2%
    position gate, not at some multiple of equity."""
    f = alloc.kelly_fraction_of_equity(
        edge_bps=31.0, sd_bps=120.0, n=400, cost_bps=12.0, t_stat=5.25, concurrency=80
    )
    assert f is not None
    assert 0.005 < f < 0.02


def test_kelly_refuses_an_edge_whose_lower_bound_is_eaten_by_costs():
    """Same point estimate, weak evidence: the 95% lower bound falls under the
    round trip, so the answer is 'size nothing', not 'size the same'."""
    strong = alloc.kelly_fraction_of_equity(
        edge_bps=31.0, sd_bps=120.0, n=400, cost_bps=12.0, t_stat=5.25, concurrency=80
    )
    weak = alloc.kelly_fraction_of_equity(
        edge_bps=31.0, sd_bps=120.0, n=400, cost_bps=12.0, t_stat=2.1, concurrency=80
    )
    assert weak == 0.0
    assert strong > weak


def test_kelly_scales_down_with_concurrency():
    kw = dict(edge_bps=31.0, sd_bps=120.0, n=400, cost_bps=12.0, t_stat=5.25)
    few = alloc.kelly_fraction_of_equity(concurrency=5, **kw)
    many = alloc.kelly_fraction_of_equity(concurrency=80, **kw)
    assert few > many > 0.0


def test_kelly_is_capped_even_when_variance_looks_tiny():
    f = alloc.kelly_fraction_of_equity(
        edge_bps=31.0, sd_bps=2.0, n=400, cost_bps=12.0, t_stat=9.0, concurrency=1
    )
    assert f == alloc.KELLY_MAX_F


def test_kelly_returns_none_when_it_cannot_decide():
    assert alloc.kelly_fraction_of_equity(edge_bps=31.0, sd_bps=0.0, n=400, cost_bps=12.0) is None
    assert alloc.kelly_fraction_of_equity(edge_bps=31.0, sd_bps=120.0, n=0, cost_bps=12.0) is None


def test_kelly_notional_never_exceeds_the_risk_gate():
    eq, gate = Decimal("8867"), Decimal("177.34")
    assert alloc.kelly_notional(equity=eq, max_notional=gate, kelly_f=0.05) == gate
    assert alloc.kelly_notional(equity=eq, max_notional=gate, kelly_f=0.005) == Decimal("44.34")
    assert alloc.kelly_notional(equity=eq, max_notional=gate, kelly_f=None) is None

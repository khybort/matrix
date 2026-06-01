"""Unit tests for matrix_shared.allocation."""

from __future__ import annotations

from decimal import Decimal

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

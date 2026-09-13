"""Lesson confidence = sample size × effect size (pure)."""

from __future__ import annotations

from decimal import Decimal

from agent_lessons.synthesizer import MIN_N_PER_BUCKET, _confidence


def test_confidence_zero_below_min_n():
    assert _confidence(MIN_N_PER_BUCKET - 1, Decimal("0.1")) == 0


def test_confidence_requires_effect_size_not_just_samples():
    weak = _confidence(20, Decimal("0.39"))    # z ≈ 1.0 → no significance
    strong = _confidence(20, Decimal("0.05"))  # z ≈ 4.0 → full significance
    assert weak == 0
    assert strong == Decimal("0.30")  # base curve at n=20 — still under the 0.40 gate
    # A strong effect clears the decision gate once the sample is real.
    assert _confidence(60, Decimal("0.20")) >= Decimal("0.40")
    assert _confidence(60, Decimal("0.44")) < Decimal("0.40")  # z≈0.9 → nothing


def test_confidence_grows_with_n_at_fixed_effect():
    assert _confidence(200, Decimal("0.30")) > _confidence(50, Decimal("0.30")) > 0


def test_legacy_signature_without_win_rate_keeps_sample_curve():
    assert _confidence(200) == Decimal("0.95")

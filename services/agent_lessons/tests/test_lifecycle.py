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


def test_buckets_count_episodes_not_rows():
    """One losing bet re-emitted 60 times inside its horizon is one sample:
    per row it cleared MIN_N with a 0% win rate and fired as an avoid."""
    from datetime import UTC, datetime, timedelta

    from agent_lessons.synthesizer import _stats

    t0 = datetime(2026, 10, 1, tzinfo=UTC)
    rows = [{"strategy_id": "matrix_agent", "asset_class": "crypto", "symbol": "XUSDT",
             "side": "short", "generated_at": t0 + timedelta(seconds=10 * i),
             "horizon_seconds": 600, "regime": "high/down/neg", "pnl_usd": -1.0}
            for i in range(60)]
    [st] = _stats(rows, key=lambda r: (r["symbol"], r["side"]),
                  bucket=lambda k: (f"{k[0]}/{k[1]}", {"symbol": k[0], "side": k[1]}, "d"))
    assert (st.n, st.n_raw, st.wins) == (1, 60, 0)
    assert st.total_pnl == Decimal("-60") and st.avg_pnl == Decimal("-60")
    assert _confidence(st.n, st.win_rate) == 0
    assert _confidence(st.n_raw, Decimal("0")) >= Decimal("0.40")  # the old reading

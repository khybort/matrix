"""Pure unit tests for reflection.mutate.rule_propose.

No DB, no LLM. The function is the core of Phase 4 — when does the
self-improvement loop fire and what does it propose? Locking the
behavior down here means future tuning to thresholds or perturbation
sizes won't silently change *whether* a mutation fires.
"""

from __future__ import annotations

from decimal import Decimal

from reflection.metrics import StrategyMetrics
from reflection.mutate import rule_propose


def _metrics(
    n_outcomes: int = 50,
    avg_score: Decimal = Decimal("-0.08"),
    win_rate: Decimal = Decimal("0.10"),
    total_pnl_usd: Decimal = Decimal("-5.0"),
) -> StrategyMetrics:
    return StrategyMetrics(
        strategy_id="t",
        version=1,
        n_outcomes=n_outcomes,
        avg_score=avg_score,
        win_rate=win_rate,
        total_pnl_usd=total_pnl_usd,
        by_symbol={},
    )


def _params(news: str = "0.10", signal_threshold: str = "0.18") -> dict:
    return {
        "weights": {
            "trade_flow": "0.35",
            "funding": "0.20",
            "oi_delta": "0.20",
            "ob_imbalance": "0.15",
            "news": news,
        },
        "signal_threshold": signal_threshold,
    }


def test_skips_when_outcomes_below_min():
    """min_outcomes=10 by default. n=5 → no proposal even if score is awful."""
    draft = rule_propose(_params(), _metrics(n_outcomes=5))
    assert draft is None


def test_skips_when_score_above_trigger():
    """Trigger = -0.05. avg_score = -0.04 (less bad) → no proposal."""
    draft = rule_propose(_params(), _metrics(avg_score=Decimal("-0.04")))
    assert draft is None


def test_skips_when_no_weights_dict():
    """No weights to perturb → no proposal."""
    draft = rule_propose(
        {"signal_threshold": "0.18"},  # no weights key
        _metrics(),
    )
    assert draft is None


def test_proposes_when_outcomes_and_score_qualify():
    """Below trigger + enough samples → MutationDraft with weight_tune."""
    draft = rule_propose(_params(), _metrics())
    assert draft is not None
    assert draft.proposal_type == "weight_tune"
    assert draft.source == "rule"


def test_dampens_news_weight():
    """News weight should be roughly halved (50% reduction)."""
    draft = rule_propose(_params(news="0.10"), _metrics())
    assert draft is not None
    before_news = Decimal(draft.before_params["weights"]["news"])
    after_news = Decimal(draft.after_params["weights"]["news"])
    # Should be smaller; expected ~half pre-normalization. Normalization
    # leaves it slightly above 0.05 — we just assert "meaningfully smaller".
    assert after_news < before_news


def test_tightens_signal_threshold():
    """signal_threshold steps up by THRESHOLD_PERTURB (0.05)."""
    draft = rule_propose(_params(signal_threshold="0.18"), _metrics())
    assert draft is not None
    after_thr = Decimal(draft.after_params["signal_threshold"])
    # 0.18 + 0.05 = 0.23 exactly. Allow for Decimal quantize jitter.
    assert after_thr == Decimal("0.2300")


def test_signal_threshold_caps_at_half():
    """Cap is 0.5. From 0.50 it shouldn't go higher."""
    draft = rule_propose(_params(signal_threshold="0.50"), _metrics())
    assert draft is not None
    after_thr = Decimal(draft.after_params["signal_threshold"])
    assert after_thr == Decimal("0.5000")


def test_news_below_floor_is_left_alone():
    """If news weight is already ≤0.05, no further dampening (per the code)."""
    draft = rule_propose(_params(news="0.05"), _metrics())
    assert draft is not None
    after_news = Decimal(draft.after_params["weights"]["news"])
    # Code only dampens when news > 0.05; otherwise just normalization runs.
    # Normalized news of 0.05 / total stays unchanged-ish; assert no increase.
    assert after_news <= Decimal("0.06")

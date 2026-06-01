"""Pure unit tests for reflection.mutate.rule_propose.

No DB, no LLM. The function is the core of Phase 4 — when does the
self-improvement loop fire and what does it propose?
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


def _params(
    news: str = "0.10",
    signal_threshold: str = "0.18",
    horizon_seconds: int = 120,
) -> dict:
    return {
        "weights": {
            "trade_flow": "0.35",
            "funding": "0.20",
            "oi_delta": "0.20",
            "ob_imbalance": "0.15",
            "news": news,
        },
        "signal_threshold": signal_threshold,
        "horizon_seconds": horizon_seconds,
        "explore_epsilon": 0.15,
    }


def test_skips_when_outcomes_below_min():
    draft = rule_propose(_params(), _metrics(n_outcomes=5))
    assert draft is None


def test_skips_when_pnl_positive_and_score_above_trigger():
    draft = rule_propose(
        _params(),
        _metrics(avg_score=Decimal("-0.04"), total_pnl_usd=Decimal("1.0")),
    )
    assert draft is None


def test_fires_on_negative_pnl_even_if_score_mild():
    draft = rule_propose(
        _params(),
        _metrics(avg_score=Decimal("-0.04"), total_pnl_usd=Decimal("-1.0")),
    )
    assert draft is not None


def test_skips_when_no_weights_dict():
    draft = rule_propose({"signal_threshold": "0.18"}, _metrics())
    assert draft is None


def test_proposes_when_outcomes_and_score_qualify():
    draft = rule_propose(_params(), _metrics())
    assert draft is not None
    assert draft.proposal_type == "weight_tune"
    assert draft.source == "rule"


def test_boosts_oi_delta_and_news_weights():
    draft = rule_propose(_params(news="0.10"), _metrics())
    assert draft is not None
    after = draft.after_params["weights"]
    before = draft.before_params["weights"]
    assert Decimal(after["oi_delta"]) > Decimal(before["oi_delta"])
    assert Decimal(after["news"]) > Decimal(before["news"])


def test_loosens_signal_threshold_when_losing():
    draft = rule_propose(_params(signal_threshold="0.18"), _metrics())
    assert draft is not None
    after_thr = Decimal(draft.after_params["signal_threshold"])
    assert after_thr < Decimal("0.18")


def test_extends_horizon_when_short():
    draft = rule_propose(_params(horizon_seconds=120), _metrics())
    assert draft is not None
    assert int(draft.after_params["horizon_seconds"]) > 120


def test_reduces_explore_epsilon():
    draft = rule_propose(_params(), _metrics())
    assert draft is not None
    assert draft.after_params["explore_epsilon"] < 0.15


def test_preserves_unmentioned_params_in_after():
    params = _params()
    params["tp_pct"] = "0.02"
    draft = rule_propose(params, _metrics())
    assert draft is not None
    assert draft.after_params.get("tp_pct") == "0.02"

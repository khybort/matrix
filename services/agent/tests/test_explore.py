"""Epsilon-greedy exploration: take more paper trades to learn faster.

`maybe_explore` flips a `hold` into a low-confidence directional trade with
probability epsilon, tagged is_exploration. It runs BEFORE the lessons gate
(so `avoid` lessons can still veto an exploratory trade) and respects BIST
long-only. Non-hold decisions are never altered. Paper-trade only — it does
not touch the live-execution certificate gate.
"""

from __future__ import annotations

from decimal import Decimal

from agent.decide import Decision, maybe_explore

EXPLORE_CONF = Decimal("0.05")


def _hold(total: str) -> Decision:
    return Decision(
        symbol="BTCUSDT",
        side="hold",
        confidence=Decimal("0"),
        thesis="t",
        method="rule",
        feature_dump={"total": total},
        last_price=Decimal("100"),
    )


def test_non_hold_decision_is_never_altered():
    d = Decision("BTCUSDT", "long", Decimal("0.3"), "t", "rule", {"total": "0.3"})
    out = maybe_explore(d, epsilon=1.0, roll=0.0, asset_class="crypto", explore_conf=EXPLORE_CONF)
    assert out is d


def test_roll_above_epsilon_keeps_hold():
    out = maybe_explore(_hold("0.05"), epsilon=0.15, roll=0.9, asset_class="crypto",
                        explore_conf=EXPLORE_CONF)
    assert out.side == "hold"


def test_explore_flips_hold_to_long_on_positive_lean():
    out = maybe_explore(_hold("0.05"), epsilon=0.15, roll=0.01, asset_class="crypto",
                        explore_conf=EXPLORE_CONF)
    assert out.side == "long"
    assert out.confidence == EXPLORE_CONF
    assert out.feature_dump["is_exploration"] is True


def test_explore_flips_hold_to_short_on_negative_lean_crypto():
    out = maybe_explore(_hold("-0.04"), epsilon=0.15, roll=0.01, asset_class="crypto",
                        explore_conf=EXPLORE_CONF)
    assert out.side == "short"
    assert out.feature_dump["is_exploration"] is True


def test_bist_does_not_explore_short():
    out = maybe_explore(_hold("-0.04"), epsilon=0.15, roll=0.01, asset_class="bist",
                        explore_conf=EXPLORE_CONF)
    assert out.side == "hold"


def test_bist_explores_long():
    out = maybe_explore(_hold("0.04"), epsilon=0.15, roll=0.01, asset_class="bist",
                        explore_conf=EXPLORE_CONF)
    assert out.side == "long"
    assert out.feature_dump["is_exploration"] is True


def test_zero_epsilon_never_explores():
    out = maybe_explore(_hold("0.05"), epsilon=0.0, roll=0.0, asset_class="crypto",
                        explore_conf=EXPLORE_CONF)
    assert out.side == "hold"

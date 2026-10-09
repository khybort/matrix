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


# --- budget sized to information (2026-10-09) --------------------------------
# Outside a corridor cell ε is cut to the maintenance share; a cell under an
# active, confident `avoid` lesson is explored at the corridor multiple, since
# only there can a probe change a decision (retire the lock).

import pytest

from agent import decide as D
from matrix_shared.agent_lessons import LessonHit


def test_maintenance_rate_is_a_share_of_epsilon():
    eps = D.explore_epsilon_for(0.05, None, corridor=False)
    assert eps == pytest.approx(0.05 * D.EXPLORE_MAINTENANCE_SHARE)
    assert 0 < eps < 0.05
    # a roll the old full-ε rule would have explored now holds
    out = maybe_explore(_hold("0.05"), epsilon=0.05, roll=0.03, asset_class="crypto")
    assert out.side == "hold"


def test_negative_edge_symbol_still_not_explored_at_maintenance():
    assert D.explore_epsilon_for(0.05, 0.2, corridor=False) == 0.0


def test_corridor_cell_explored_at_multiple_and_ignores_edge_scale():
    assert D.explore_epsilon_for(0.05, 0.2, corridor=True) == pytest.approx(0.05 * D.EXPLORE_CORRIDOR_MULT)
    assert D.explore_epsilon_for(0.5, None, corridor=True) == 1.0
    out = maybe_explore(_hold("0.05"), epsilon=0.05, roll=0.10, asset_class="crypto",
                        symbol_edge=0.2, corridor=True)
    assert out.side == "long"
    assert out.feature_dump["explore_cell"] == "corridor"


class _F:
    symbol = "BTCUSDT"


def _lesson(desc: str, conf: str, verdict: str = "avoid") -> LessonHit:
    return LessonHit(lesson_id="L1", verdict=verdict, pattern_description=desc,
                     confidence=Decimal(conf), win_rate=Decimal("0.2"), n_observations=40)


@pytest.mark.asyncio
@pytest.mark.parametrize("hits,expect", [
    ([_lesson("long on BTCUSDT", "0.8")], "long"),            # corridor → explored at 3ε
    ([_lesson("long on BTCUSDT", "0.2")], "hold"),            # below gate → maintenance
    ([_lesson("OPERATOR: no longs", "0.99")], "hold"),        # operator lock is never probed
    ([_lesson("long on BTCUSDT", "0.8", "prefer")], "hold"),  # nothing to retire
    ([], "hold"),
])
async def test_explore_routes_probes_to_corridor_cells(monkeypatch, hits, expect):
    seen = {}

    async def fake(features, strategy_id, *, side=None, asset_class=None):
        seen["side"] = side
        return hits

    monkeypatch.setattr(D, "lessons_relevant_to", fake)
    # roll above the maintenance rate but below the corridor rate
    out = await D.explore(_hold("0.05"), _F(), epsilon=0.05, roll=0.10,
                          strategy_id="matrix_agent", asset_class="crypto")
    assert out.side == expect
    assert seen["side"] == "long"  # looked up on the probe's lean side


@pytest.mark.asyncio
async def test_explore_skips_lesson_lookup_when_roll_cannot_fire(monkeypatch):
    async def boom(*a, **k):
        raise AssertionError("no lookup expected")

    monkeypatch.setattr(D, "lessons_relevant_to", boom)
    out = await D.explore(_hold("0.05"), _F(), epsilon=0.05, roll=0.9,
                          strategy_id="matrix_agent", asset_class="crypto")
    assert out.side == "hold"


@pytest.mark.asyncio
async def test_explore_lookup_failure_falls_back_to_maintenance(monkeypatch):
    async def fail(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(D, "lessons_relevant_to", fail)
    out = await D.explore(_hold("0.05"), _F(), epsilon=0.05, roll=0.001,
                          strategy_id="matrix_agent", asset_class="crypto")
    assert out.side == "long" and out.feature_dump["explore_cell"] == "maintenance"

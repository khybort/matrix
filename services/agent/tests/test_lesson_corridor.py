"""_apply_lessons: exploration corridor lets a share of avoid-vetoed
exploration trades through, tagged lesson_bypass; normal trades still vetoed."""

from __future__ import annotations

from decimal import Decimal

import pytest

from agent import decide as D
from matrix_shared.agent_lessons import LessonHit

pytestmark = pytest.mark.asyncio


class _F:
    symbol = "BTCUSDT"


def _hit():
    return LessonHit(lesson_id="L1", verdict="avoid", pattern_description="long on BTCUSDT",
                     confidence=Decimal("0.8"), win_rate=Decimal("0.2"), n_observations=40)


def _decision(explore: bool):
    return D.Decision(symbol="BTCUSDT", side="long", confidence=Decimal("0.05") if explore else Decimal("0.6"),
                      thesis="t", method="rule+explore" if explore else "rule",
                      feature_dump={"is_exploration": explore}, last_price=Decimal("100"))


@pytest.fixture(autouse=True)
def _patch(monkeypatch):
    async def fake(features, strategy_id, *, side=None, asset_class=None):
        assert asset_class == "crypto"
        return [_hit()]
    monkeypatch.setattr(D, "lessons_relevant_to", fake)
    monkeypatch.setattr(D, "LESSON_EXPLORE_BYPASS", 0.25)


async def test_normal_trade_is_vetoed_regardless_of_roll():
    d = await D._apply_lessons(_decision(False), _F(), "matrix_agent", asset_class="crypto", bypass_roll=0.0)
    assert d.side == "hold" and d.feature_dump["lesson_override"]["lesson_id"] == "L1"


async def test_exploration_trade_bypasses_within_corridor():
    d = await D._apply_lessons(_decision(True), _F(), "matrix_agent", asset_class="crypto", bypass_roll=0.1)
    assert d.side == "long" and d.feature_dump["lesson_bypass"] == "L1"
    assert d.method.endswith("+bypass")


async def test_exploration_trade_vetoed_outside_corridor():
    d = await D._apply_lessons(_decision(True), _F(), "matrix_agent", asset_class="crypto", bypass_roll=0.9)
    assert d.side == "hold"


async def test_operator_directive_is_never_bypassed(monkeypatch):
    async def fake(features, strategy_id, *, side=None, asset_class=None):
        return [LessonHit(lesson_id="OP", verdict="avoid", pattern_description="OPERATOR: avoid long on BTCUSDT — stop",
                          confidence=Decimal("0.99"), win_rate=None, n_observations=0)]
    monkeypatch.setattr(D, "lessons_relevant_to", fake)
    d = await D._apply_lessons(_decision(True), _F(), "matrix_agent", asset_class="crypto", bypass_roll=0.0)
    assert d.side == "hold" and d.feature_dump["lesson_override"]["lesson_id"] == "OP"

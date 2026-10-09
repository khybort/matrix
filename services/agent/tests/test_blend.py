"""blend_decisions: the LLM no longer overrides the rule model unconditionally."""

from __future__ import annotations

from decimal import Decimal

import pytest

from agent import decide as D
from agent.llm import LLMDecision


class _F:
    symbol = "BTCUSDT"
    last_price = Decimal("100")


def _rule(side, conf="0.4"):
    return D.Decision(symbol="BTCUSDT", side=side, confidence=Decimal(conf), thesis="r",
                      method="rule", feature_dump={"total": "0.2"}, last_price=Decimal("100"))


@pytest.fixture(autouse=True)
def _blend_mode(monkeypatch):
    monkeypatch.setattr(D, "BLEND_MODE", "blend")


def test_agreement_averages_confidence():
    d = D.blend_decisions(_rule("long", "0.4"), LLMDecision("long", 0.8, "x"), _F())
    assert d.side == "long" and d.confidence == Decimal("0.6") and d.method == "llm+rule"
    assert d.feature_dump["rule_side"] == "long" and d.feature_dump["llm_side"] == "long"


def test_conflict_forces_hold():
    d = D.blend_decisions(_rule("long"), LLMDecision("short", 0.9, "x"), _F())
    assert d.side == "hold" and d.method == "conflict" and d.confidence == 0


def test_solo_sides_are_dampened():
    d = D.blend_decisions(_rule("hold", "0"), LLMDecision("short", 0.5, "x"), _F())
    assert d.side == "short" and d.method == "llm" and d.confidence == Decimal("0.5") * Decimal("0.6")
    d = D.blend_decisions(_rule("long", "0.5"), LLMDecision("hold", 0.2, "x"), _F())
    assert d.side == "long" and d.method == "rule" and d.confidence == Decimal("0.5") * Decimal("0.6")


def test_no_llm_returns_rule_unchanged():
    r = _rule("long")
    assert D.blend_decisions(r, None, _F()) is r


def test_legacy_override_mode(monkeypatch):
    monkeypatch.setattr(D, "BLEND_MODE", "llm_overrides")
    d = D.blend_decisions(_rule("long"), LLMDecision("short", 0.9, "x"), _F())
    assert d.side == "short" and d.method == "llm"


def test_rule_only_mode_ignores_llm(monkeypatch):
    monkeypatch.setattr(D, "BLEND_MODE", "rule_only")
    r = _rule("long")
    assert D.blend_decisions(r, LLMDecision("short", 0.9, "x"), _F()) is r


def test_prompt_carries_graph_rule_and_lessons():
    from matrix_shared.agent_lessons import LessonHit
    from agent.features import SymbolFeatures

    f = SymbolFeatures(symbol="BTCUSDT", last_price=Decimal("100"), graph_mention_count=3,
                       graph_direct_polarity=Decimal("-0.4"), graph_related_companies=["BlackRock"])
    hit = LessonHit(lesson_id="L", verdict="avoid", pattern_description="long on BTCUSDT",
                    confidence=Decimal("0.7"), win_rate=Decimal("0.3"), n_observations=40)
    text = D._llm_prompt(f, rule=_rule("long"), lessons=[hit], symbol_edge=0.42)
    assert "Knowledge graph" in text and "BlackRock" in text
    assert "Quant model verdict: LONG" in text
    assert "AVOID long on BTCUSDT" in text and "Realised edge" in text


def test_rule_only_mode_skips_the_llm_call(monkeypatch):
    """rule_only must not pay for a call whose answer it discards."""
    import asyncio

    from agent.features import SymbolFeatures

    monkeypatch.setattr(D, "BLEND_MODE", "rule_only")
    monkeypatch.setattr(D, "llm_enabled", lambda: True)
    assert D._ask_llm() is False

    async def _boom(*a, **k):
        raise AssertionError("LLM called in rule_only mode")

    async def _same(d, *a, **k):
        return d

    monkeypatch.setattr(D, "call_llm_decisions_batch", _boom)
    monkeypatch.setattr(D, "_apply_lessons", _same)
    monkeypatch.setattr(D, "_apply_setup_memory", _same)
    f = SymbolFeatures(symbol="BTCUSDT", last_price=Decimal("100"))
    out = asyncio.run(D.decide_batch([(f, None, "crypto", None)], explore_rand=lambda: 1.0))
    assert len(out) == 1 and out[0].method == "rule"

    monkeypatch.setattr(D, "BLEND_MODE", "blend")
    assert D._ask_llm() is True

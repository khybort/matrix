"""Pure-function tests for matrix_shared.agent_lessons matching logic.

No DB. Builds AgentLesson instances directly and asserts the predicate
behavior for each pattern_kind.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from matrix_shared.agent_lessons import matches
from matrix_shared.models import AgentLesson


@dataclass
class _Features:
    symbol: str = "BTCUSDT"
    buy_share_60s: Decimal = Decimal("0.5")
    funding_rate: Decimal | None = None
    n_trades_60s: int = 50


def _lesson(kind: str, filt: dict, verdict: str = "avoid") -> AgentLesson:
    return AgentLesson(
        strategy_id="matrix_agent",
        strategy_version=1,
        pattern_kind=kind,
        pattern_description="test",
        pattern_filter=filt,
        n_observations=50,
        win_rate=Decimal("0.3"),
        avg_pnl_usd=Decimal("-0.1"),
        total_pnl_usd=Decimal("-5"),
        verdict=verdict,
        confidence=Decimal("0.6"),
        observed_from=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        observed_until=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        generated_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        status="active",
    )


# ---- symbol_specific ----


def test_symbol_specific_matches_exact():
    lesson = _lesson("symbol_specific", {"symbol": "BTCUSDT", "side": "short"})
    f = _Features(symbol="BTCUSDT")
    assert matches(lesson, f, side="short")
    assert not matches(lesson, f, side="long")
    assert not matches(lesson, _Features(symbol="ETHUSDT"), side="short")


def test_symbol_specific_matches_symbol_only():
    lesson = _lesson("symbol_specific", {"symbol": "BTCUSDT"})
    assert matches(lesson, _Features(symbol="BTCUSDT"), side="long")
    assert matches(lesson, _Features(symbol="BTCUSDT"), side="short")
    assert not matches(lesson, _Features(symbol="ETHUSDT"), side="long")


# ---- feature_value_band ----


def test_band_matches_inside_range():
    lesson = _lesson("feature_value_band", {
        "feature": "buy_share_60s", "min": 0.7, "max": 1.0,
    })
    assert matches(lesson, _Features(buy_share_60s=Decimal("0.85")), side="long")
    assert not matches(lesson, _Features(buy_share_60s=Decimal("0.5")), side="long")
    assert not matches(lesson, _Features(buy_share_60s=Decimal("1.1")), side="long")


def test_band_inclusive_endpoints():
    lesson = _lesson("feature_value_band", {
        "feature": "buy_share_60s", "min": 0.7, "max": 1.0,
    })
    # The predicate uses `< min` and `> max`, so equals are inside.
    assert matches(lesson, _Features(buy_share_60s=Decimal("0.7")), side="long")
    assert matches(lesson, _Features(buy_share_60s=Decimal("1.0")), side="long")


def test_band_with_side_constraint():
    lesson = _lesson("feature_value_band", {
        "feature": "buy_share_60s", "min": 0.7, "max": 1.0, "side": "short",
    })
    assert matches(lesson, _Features(buy_share_60s=Decimal("0.85")), side="short")
    assert not matches(lesson, _Features(buy_share_60s=Decimal("0.85")), side="long")


def test_band_returns_false_when_feature_missing():
    """funding_rate=None on the feature object → never matches."""
    lesson = _lesson("feature_value_band", {
        "feature": "funding_rate", "min": 0.0001, "max": 0.001,
    })
    assert not matches(lesson, _Features(funding_rate=None), side="long")


# ---- feature_threshold ----


def test_threshold_ge():
    lesson = _lesson("feature_threshold", {
        "feature": "funding_rate", "op": ">=", "value": 0.0001,
    })
    assert matches(lesson, _Features(funding_rate=Decimal("0.0002")), side="long")
    assert matches(lesson, _Features(funding_rate=Decimal("0.0001")), side="long")
    assert not matches(lesson, _Features(funding_rate=Decimal("0.00005")), side="long")


def test_threshold_lt():
    lesson = _lesson("feature_threshold", {
        "feature": "n_trades_60s", "op": "<", "value": 10,
    })
    assert matches(lesson, _Features(n_trades_60s=5), side="long")
    assert not matches(lesson, _Features(n_trades_60s=10), side="long")


def test_unknown_pattern_kind_never_matches():
    lesson = _lesson("future_kind_we_dont_understand", {"any": "thing"})
    assert not matches(lesson, _Features(), side="long")

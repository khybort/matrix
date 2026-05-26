"""Helpers for matching agent_lessons against current feature state.

The agent process (services/agent — FORBIDDEN_PATHS) will eventually
call `lessons_relevant_to(features, strategy_id)` per tick. We keep
this in matrix_shared so the integration is a one-import change in
the agent's decide() once the operator green-lights it.

Pattern filter schema (JSON in agent_lessons.pattern_filter):

  feature_value_band:
    {"feature": "buy_share_60s", "min": 0.7, "max": 1.0, "side": "short"}
    matches when features.buy_share_60s ∈ [0.7, 1.0] AND decided side = "short"

  symbol_specific:
    {"symbol": "BTCUSDT", "side": "long"}
    matches when prediction is for that symbol+side combo

  feature_threshold:
    {"feature": "funding_rate", "op": ">=", "value": 0.0001}
    one-sided gate
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from matrix_shared.db import shared_session_scope
from matrix_shared.models import AgentLesson


@dataclass(slots=True)
class LessonHit:
    """A lesson that matched current state, with the action it implies."""

    lesson_id: str
    verdict: str   # "avoid" | "prefer" | "neutral"
    pattern_description: str
    confidence: Decimal
    win_rate: Decimal | None
    n_observations: int


def _value_for(features: Any, feature_name: str) -> Any:
    """Read a feature attribute by string name. Tolerates None and missing."""
    return getattr(features, feature_name, None)


def _band_matches(filt: dict[str, Any], features: Any, side: str | None) -> bool:
    feature_name = filt.get("feature")
    if not feature_name:
        return False
    raw = _value_for(features, feature_name)
    if raw is None:
        return False
    try:
        v = Decimal(str(raw))
    except (ValueError, ArithmeticError):
        return False
    lo = Decimal(str(filt["min"])) if "min" in filt else None
    hi = Decimal(str(filt["max"])) if "max" in filt else None
    if lo is not None and v < lo:
        return False
    if hi is not None and v > hi:
        return False
    required_side = filt.get("side")
    if required_side and side and required_side != side:
        return False
    return True


def _threshold_matches(filt: dict[str, Any], features: Any, side: str | None) -> bool:
    feature_name = filt.get("feature")
    if not feature_name:
        return False
    raw = _value_for(features, feature_name)
    if raw is None:
        return False
    try:
        v = Decimal(str(raw))
        bound = Decimal(str(filt["value"]))
    except (ValueError, ArithmeticError, KeyError):
        return False
    op = filt.get("op", ">=")
    if op == ">=" and not (v >= bound):
        return False
    if op == ">" and not (v > bound):
        return False
    if op == "<=" and not (v <= bound):
        return False
    if op == "<" and not (v < bound):
        return False
    required_side = filt.get("side")
    if required_side and side and required_side != side:
        return False
    return True


def _symbol_matches(filt: dict[str, Any], symbol: str | None, side: str | None) -> bool:
    if filt.get("symbol") and filt["symbol"] != symbol:
        return False
    if filt.get("side") and side and filt["side"] != side:
        return False
    return True


def matches(lesson: AgentLesson, features: Any, side: str | None) -> bool:
    """Pure-function predicate. No DB access — caller passes the lesson."""
    kind = lesson.pattern_kind
    filt = lesson.pattern_filter or {}
    if kind == "feature_value_band":
        return _band_matches(filt, features, side)
    if kind == "feature_threshold":
        return _threshold_matches(filt, features, side)
    if kind == "symbol_specific":
        symbol = getattr(features, "symbol", None)
        return _symbol_matches(filt, symbol, side)
    # Unknown kind → never matches; lesson would be from a future schema.
    return False


async def active_lessons(
    strategy_id: str,
    *,
    asset_class: str | None = None,
) -> list[AgentLesson]:
    """All currently-active lessons for a strategy.

    When `asset_class` is given, filter to that market (Phase E parity):
    crypto callers don't want BIST lessons informing their decisions and
    vice versa. Pass `asset_class=None` only for diagnostics / dashboards
    that want the full set.

    Caller filters by side or feature state via `matches()`.
    """
    async with shared_session_scope() as session:
        stmt = (
            select(AgentLesson)
            .where(AgentLesson.strategy_id == strategy_id)
            .where(AgentLesson.status == "active")
        )
        if asset_class is not None:
            stmt = stmt.where(AgentLesson.asset_class == asset_class)
        rows = (await session.execute(stmt)).scalars().all()
        # Detach from session so callers can read attributes after the
        # scope exits.
        for r in rows:
            session.expunge(r)
        return list(rows)


async def lessons_relevant_to(
    features: Any,
    strategy_id: str,
    *,
    side: str | None = None,
) -> list[LessonHit]:
    """The agent's per-tick consult call. Pulls every active lesson for the
    strategy and returns the subset that matches the current feature state
    (+ optional decided side). Order: 'avoid' verdicts first, then 'prefer',
    then 'neutral' — caller can short-circuit at the first 'avoid' if it
    wants to."""
    rows = await active_lessons(strategy_id)
    hits = [
        LessonHit(
            lesson_id=str(r.id),
            verdict=r.verdict,
            pattern_description=r.pattern_description,
            confidence=r.confidence if r.confidence is not None else Decimal("0"),
            win_rate=r.win_rate,
            n_observations=r.n_observations,
        )
        for r in rows
        if matches(r, features, side)
    ]
    verdict_priority = {"avoid": 0, "prefer": 1, "neutral": 2}
    hits.sort(key=lambda h: (verdict_priority.get(h.verdict, 99), -float(h.confidence)))
    return hits


__all__ = [
    "AgentLesson",
    "LessonHit",
    "active_lessons",
    "lessons_relevant_to",
    "matches",
]

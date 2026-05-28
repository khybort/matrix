"""Apply active 'avoid' agent_lessons to predictions before they're persisted.

Lessons are per-strategy verdicts produced by the lessons feeder (see
services/agent_lessons). An 'avoid' verdict means "trades matching this
filter underperformed; drop new candidates that match".

The filter is jsonb on agent_lessons.pattern_filter. A draft matches when
EVERY key/value in the filter equals the matching attribute on the draft.
Recognized keys:
    symbol, side, asset_class

We keep the dispatcher generic — strategy modules stay symbol-agnostic.
Lessons enter and leave automatically as the lessons feeder updates rows.
"""

from __future__ import annotations

import time
from collections.abc import Iterable

from loguru import logger
from sqlalchemy import select

from matrix_shared import shared_session_scope
from matrix_shared.models import AgentLesson

from strategy.base import PredictionDraft

# Re-read avoid lessons periodically so operators / reflection updates
# take effect without a service restart.
_TTL_S = 60.0
_cache: list[dict] | None = None
_cache_ts: float = 0.0

_RECOGNIZED_KEYS = ("symbol", "side", "asset_class")


async def _load_avoid_filters() -> list[dict]:
    """Return list of (strategy_id, pattern_filter) for active avoid lessons.

    Cached for _TTL_S; result is a list of dicts with keys:
        strategy_id, asset_class, pattern_filter
    """
    global _cache, _cache_ts
    now = time.monotonic()
    if _cache is not None and now - _cache_ts < _TTL_S:
        return _cache

    async with shared_session_scope() as session:
        rows = list(
            (
                await session.execute(
                    select(
                        AgentLesson.strategy_id,
                        AgentLesson.asset_class,
                        AgentLesson.pattern_filter,
                    )
                    .where(AgentLesson.status == "active")
                    .where(AgentLesson.verdict == "avoid")
                )
            ).all()
        )

    _cache = [
        {
            "strategy_id": r.strategy_id,
            "asset_class": r.asset_class,
            "filter": r.pattern_filter or {},
        }
        for r in rows
    ]
    _cache_ts = now
    return _cache


def _draft_matches(draft: PredictionDraft, lesson: dict) -> bool:
    """A draft matches a lesson when strategy_id and asset_class line up AND
    every key in the lesson's pattern_filter equals the matching draft attr.
    Unknown keys in the filter are ignored (so a typo doesn't silently block
    everything — explicit check on recognized keys only)."""
    if draft.strategy_id != lesson["strategy_id"]:
        return False
    if draft.asset_class != lesson["asset_class"]:
        return False
    pf = lesson["filter"]
    if not pf:
        return False
    for key in _RECOGNIZED_KEYS:
        if key in pf:
            if getattr(draft, key, None) != pf[key]:
                return False
    return True


async def filter_drafts(drafts: Iterable[PredictionDraft]) -> list[PredictionDraft]:
    """Drop drafts that match any active 'avoid' lesson.

    Returns the surviving list. Logs dropped counts per (strategy, symbol).
    """
    lessons = await _load_avoid_filters()
    if not lessons:
        return list(drafts)

    survivors: list[PredictionDraft] = []
    dropped: dict[tuple[str, str], int] = {}
    for d in drafts:
        if any(_draft_matches(d, L) for L in lessons):
            key = (d.strategy_id, d.symbol)
            dropped[key] = dropped.get(key, 0) + 1
            continue
        survivors.append(d)

    if dropped:
        for (sid, sym), n in dropped.items():
            logger.info(f"lessons: dropped {n} {sid} drafts on {sym} (avoid lesson)")
    return survivors

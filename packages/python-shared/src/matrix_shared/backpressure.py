"""Prediction backlog backpressure.

A strategy with 1 allocated slot was emitting ~9,000 predictions a day of
which 36 traded (funding_reversion, 2026-09-13); matrix_agent burned an LLM
call for 1,441 predictions that expired unfilled. Everything past a few times
the slot count is pure cost: DB churn, LLM tokens, and a noisier candidate
pool for the paper engine's EV sort.

`room()` = how many more open, unfilled predictions a strategy may have on a
market right now: cap − backlog, where

    cap     = max(BACKLOG_MIN, allocated_slots(champion wallet) × BACKLOG_MULT)
    backlog = open predictions inside their horizon with no paper position

Challenger (shadow) predictions are counted separately against the same cap
(they borrow the champion's slots in the paper engine too). Advisory: on any
DB error `room()` returns `BACKLOG_MIN` so a probe failure never silences a
strategy completely.
"""

from __future__ import annotations

import os
import time

from loguru import logger
from sqlalchemy import text

from matrix_shared.db import shared_session_scope

BACKLOG_MULT = int(os.environ.get("MATRIX_BACKLOG_SLOTS_MULT", "5"))
BACKLOG_MIN = int(os.environ.get("MATRIX_BACKLOG_MIN", "3"))
DEFAULT_SLOTS = int(os.environ.get("MATRIX_BACKLOG_DEFAULT_SLOTS", "2"))  # no slot row yet
SLOTS_TTL_S = 60.0

_slots_cache: dict[tuple[str, str], tuple[float, int]] = {}


def cap_for_slots(slots: int | None) -> int:
    s = DEFAULT_SLOTS if slots is None else max(0, int(slots))
    return max(BACKLOG_MIN, s * BACKLOG_MULT)


async def champion_slots(strategy_id: str, asset_class: str) -> int | None:
    key = (strategy_id, asset_class)
    hit = _slots_cache.get(key)
    now = time.monotonic()
    if hit and now - hit[0] < SLOTS_TTL_S:
        return hit[1]
    async with shared_session_scope() as session:
        slots = (await session.execute(text(
            "SELECT c.allocated_slots FROM strategy_slot_configs c "
            "JOIN wallets w ON w.id = c.wallet_id "
            "WHERE c.strategy_id = :sid AND c.asset_class = :ac AND w.name <> 'shadow' "
            "ORDER BY w.created_at ASC LIMIT 1"
        ), {"sid": strategy_id, "ac": asset_class})).scalar()
    val = int(slots) if slots is not None else None
    _slots_cache[key] = (now, val)
    return val


async def backlog(strategy_id: str, asset_class: str, *, shadow: bool = False) -> int:
    async with shared_session_scope() as session:
        n = (await session.execute(text(
            "SELECT count(*) FROM predictions p "
            "LEFT JOIN paper_positions pp ON pp.prediction_id = p.id "
            "WHERE p.strategy_id = :sid AND p.asset_class = :ac AND p.status = 'open' "
            "AND p.close_by > now() AND pp.id IS NULL "
            "AND coalesce(p.context->>'is_shadow', 'false') = :sh"
        ), {"sid": strategy_id, "ac": asset_class, "sh": "true" if shadow else "false"})).scalar()
    return int(n or 0)


async def room(strategy_id: str, asset_class: str, *, shadow: bool = False) -> int:
    """How many new predictions this strategy may add right now (>= 0)."""
    try:
        cap = cap_for_slots(await champion_slots(strategy_id, asset_class))
        have = await backlog(strategy_id, asset_class, shadow=shadow)
    except Exception as e:  # noqa: BLE001 — advisory
        logger.debug(f"backpressure: probe failed for {strategy_id}/{asset_class} ({e})")
        return BACKLOG_MIN
    return max(0, cap - have)


def clear_cache() -> None:
    _slots_cache.clear()

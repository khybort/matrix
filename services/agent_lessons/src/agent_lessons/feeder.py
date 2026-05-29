"""Lessons → dev_tasks feeder.

Crypto-priority loop: every scan, look at active `avoid` lessons that
matter (confidence >= MIN_CONFIDENCE) and enqueue ONE dev_task asking
dev_agent to address the failing pattern. Rate-limited to at most one
new task per RATE_LIMIT_MINUTES so a flood of avoid verdicts can't
exhaust the operator's subscription quota.

Dedup is signature-based: a task carries the marker
`[lessons-feeder:{strategy}/{symbol}/{side}]` in its description so
the next scan can skip patterns already in flight or recently shipped.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import asyncpg
from loguru import logger
from matrix_shared import shared_session_scope
from matrix_shared.agent_lessons import active_lessons
from matrix_shared.models import StrategyConfig
from matrix_shared.subscription_llm import MODEL_SONNET
from sqlalchemy import select

# Lessons below this confidence don't trigger a task — too noisy.
MIN_CONFIDENCE = Decimal("0.4")
# At most one new task per N minutes across all strategies.
RATE_LIMIT_MINUTES = 60
# Dedup window: if a task for the same signature was enqueued in this
# many days, skip it.
DEDUP_DAYS = 7
# Defaults for the dev_task that gets created. max_turns set high
# (subscription is flat-rate so this only caps wall-clock not $) because
# these tasks dispatch sub-agents and need room to explore + run tests.
# dev_tasks write code + run tests; keep them on Sonnet (Haiku too weak here,
# and these run <=1/hour so they barely touch the decision loop's rate budget).
DEFAULT_MODEL = MODEL_SONNET
DEFAULT_MAX_TURNS = 60
DEFAULT_COST_CAP_USD = 5.0


def _signature(strategy_id: str, symbol: str | None, side: str | None) -> str:
    """Stable string we embed in dev_task.description for dedup queries."""
    return f"[lessons-feeder:{strategy_id}/{symbol or 'any'}/{side or 'any'}]"


def _task_description(
    strategy_id: str,
    symbol: str | None,
    side: str | None,
    pattern_description: str,
    n_observations: int,
    win_rate: Decimal | None,
    avg_pnl: Decimal | None,
) -> str:
    """Brief the dev_agent. Goal: actionable, crypto-focused, no spec rot."""
    sig = _signature(strategy_id, symbol, side)
    wr = f"{float(win_rate) * 100:.1f}%" if win_rate is not None else "unknown"
    pnl = f"${float(avg_pnl):.4f}" if avg_pnl is not None else "unknown"
    return (
        f"{sig}\n\n"
        f"agent_lessons synthesized an `avoid` verdict for `{strategy_id}` "
        f"on the pattern: \"{pattern_description}\".\n"
        f"Evidence: n={n_observations} closed trades, win_rate={wr}, "
        f"avg_pnl_per_trade={pnl}.\n\n"
        "Task: propose and apply a code change that stops this pattern from "
        "losing money. Pick ONE of these approaches, whichever fits the "
        "evidence best:\n"
        "  (a) Modify `services/agent/src/agent/decide.py` (or the relevant "
        "strategy module) to detect and skip this pattern.\n"
        "  (b) Propose a new crypto strategy module under "
        "`services/strategy/src/strategy/modules/` that targets the opposite "
        "signal (e.g. a contrarian fade of the failing direction).\n"
        "  (c) If neither (a) nor (b) is justified by the data, recommend "
        "retiring the strategy — explain why in the commit message.\n\n"
        "Hard constraints:\n"
        "  - Run the affected service's pytest before finishing.\n"
        "  - Do NOT touch matrix_shared.trading_safety or any "
        "paper_trade_certificate code.\n"
        "  - Do NOT add new external HTTP dependencies — LLM calls go through "
        "`matrix_shared.call_subscription[_json]`.\n"
        "  - Keep the change small and reviewable. One commit, < 200 lines.\n"
    )


async def _last_feeder_task_ts(pool: asyncpg.Pool) -> datetime | None:
    row = await pool.fetchrow(
        """
        SELECT MAX(created_at) AS last_ts
          FROM dev_tasks
         WHERE description LIKE '[lessons-feeder:%'
        """
    )
    return row["last_ts"] if row and row["last_ts"] else None


async def _signature_recently_seen(
    pool: asyncpg.Pool, signature: str, within_days: int
) -> bool:
    cutoff = datetime.now(UTC) - timedelta(days=within_days)
    row = await pool.fetchrow(
        """
        SELECT 1
          FROM dev_tasks
         WHERE description LIKE $1
           AND created_at >= $2
         LIMIT 1
        """,
        f"%{signature}%",
        cutoff,
    )
    return row is not None


async def _enqueue_task(pool: asyncpg.Pool, description: str) -> int:
    row = await pool.fetchrow(
        """
        INSERT INTO dev_tasks (
          source, description, priority, exclusive,
          auto_commit, auto_pr, run_tests, max_turns, model,
          cost_cap_usd, review_mode
        )
        VALUES ('reflection', $1, 5, false,
                true, false, true, $2, $3,
                $4, 'auto')
        RETURNING id
        """,
        description,
        DEFAULT_MAX_TURNS,
        DEFAULT_MODEL,
        DEFAULT_COST_CAP_USD,
    )
    return row["id"]


async def _strategy_is_active(strategy_id: str) -> bool:
    """True iff at least one strategy_configs row is currently active.

    Lessons for retired strategies are moot — no production code will ever
    consume them — so feeding them to dev_agent would just burn cycles.
    """
    async with shared_session_scope() as session:
        row = (
            await session.execute(
                select(StrategyConfig.id)
                .where(StrategyConfig.strategy_id == strategy_id)
                .where(StrategyConfig.status == "active")
                .limit(1)
            )
        ).first()
        return row is not None


async def feed_once(
    strategy_id: str = "matrix_agent",
    *,
    asset_class: str = "crypto",
) -> dict[str, Any]:
    """One scan: returns {'enqueued_task_id': int|None, 'reason': str}.

    Scoped to one (strategy_id, asset_class) pair so crypto and BIST
    lesson feeds never blur. Idempotent — safe to call from any cadence
    loop. Internal rate limit + dedup do the right thing.
    """
    if not await _strategy_is_active(strategy_id):
        return {
            "enqueued_task_id": None,
            "reason": f"{strategy_id} has no active strategy_configs row — lessons moot",
        }

    local_url = os.environ.get("LOCAL_DATABASE_URL")
    if not local_url:
        return {"enqueued_task_id": None, "reason": "LOCAL_DATABASE_URL not set"}

    pool = await asyncpg.create_pool(local_url, min_size=1, max_size=2)
    try:
        last_ts = await _last_feeder_task_ts(pool)
        if last_ts is not None:
            since = datetime.now(UTC) - last_ts
            if since < timedelta(minutes=RATE_LIMIT_MINUTES):
                mins_left = RATE_LIMIT_MINUTES - int(since.total_seconds() // 60)
                return {
                    "enqueued_task_id": None,
                    "reason": f"rate-limited ({mins_left}min until next slot)",
                }

        lessons = await active_lessons(strategy_id, asset_class=asset_class)
        candidates = [
            l for l in lessons
            if l.verdict == "avoid"
            and l.confidence is not None
            and l.confidence >= MIN_CONFIDENCE
        ]
        if not candidates:
            return {
                "enqueued_task_id": None,
                "reason": (
                    f"no qualifying avoid lessons for {strategy_id}/{asset_class}"
                ),
            }

        # Highest confidence first; ties broken by larger n_observations.
        candidates.sort(
            key=lambda l: (float(l.confidence or 0), l.n_observations),
            reverse=True,
        )

        for lesson in candidates:
            filt = lesson.pattern_filter or {}
            symbol = filt.get("symbol")
            side = filt.get("side")
            sig = _signature(lesson.strategy_id, symbol, side)
            if await _signature_recently_seen(pool, sig, DEDUP_DAYS):
                continue
            description = _task_description(
                strategy_id=lesson.strategy_id,
                symbol=symbol,
                side=side,
                pattern_description=lesson.pattern_description,
                n_observations=lesson.n_observations,
                win_rate=lesson.win_rate,
                avg_pnl=lesson.avg_pnl_usd,
            )
            task_id = await _enqueue_task(pool, description)
            logger.info(
                f"enqueued dev_task #{task_id} for "
                f"{lesson.strategy_id}/{symbol}/{side} "
                f"(n={lesson.n_observations}, conf={lesson.confidence})"
            )
            return {
                "enqueued_task_id": task_id,
                "reason": f"enqueued for {sig}",
            }

        return {
            "enqueued_task_id": None,
            "reason": "all qualifying lessons already seen within dedup window",
        }
    finally:
        await pool.close()

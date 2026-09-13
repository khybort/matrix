"""Distill agent decisions + outcomes into actionable lessons.

Inputs (read-only, SHARED tier):
  predictions  — strategy_id, side, symbol, confidence, generated_at,
                 thesis (carries feature dump as text — we'll regex some
                 of it for now; cleaner extraction is a follow-up)
  outcomes     — pnl_usd, score, observed_at

For each `pattern_kind`:
  1. Group historical decisions over the lookback window by the pattern's
     bucket function (e.g. buy_share_60s in deciles, or symbol+side).
  2. For each bucket, compute n, win_rate, total_pnl_usd.
  3. If n ≥ MIN_N AND win_rate strays from chance (50%) by ≥ DEVIATION,
     emit a lesson with verdict=avoid (low win rate) or prefer (high).
  4. Supersede the prior active lesson for the same (strategy, kind,
     bucket key) if one exists — keeps the table from accumulating
     redundant rows.

Pattern kinds: `symbol_specific` (symbol × side) and `regime` (market regime
× side, from predictions.context.regime — matrix_shared.regime). Operator
directives share the table but are never rewritten here.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from loguru import logger
from sqlalchemy import case, desc, func, select, update

from matrix_shared import shared_session_scope
from matrix_shared.models import AgentLesson, Outcome, Prediction

DEFAULT_STRATEGY_ID = "matrix_agent"
LOOKBACK_HOURS = 24 * 7   # 7-day rolling window
MIN_N_PER_BUCKET = 20      # below this we ignore — too noisy
WIN_RATE_DEVIATION = Decimal("0.10")  # 50% ± 10% → either verdict
# Lifecycle (docs/AUTONOMY_PLAN.md P2.1): a lesson that is not re-confirmed by
# fresh outcomes expires; a lesson contradicted by exploration-corridor trades
# (predictions.context.lesson_bypass) is retired early.
LESSON_TTL_DAYS = int(os.environ.get("MATRIX_LESSON_TTL_DAYS", "14"))
BYPASS_MIN_N = int(os.environ.get("MATRIX_LESSON_BYPASS_MIN_N", "10"))


@dataclass(slots=True)
class _PatternStat:
    bucket_key: str
    bucket_filter: dict[str, Any]
    description: str
    n: int
    wins: int
    total_pnl: Decimal
    win_rate: Decimal
    avg_pnl: Decimal


def _verdict_for(win_rate: Decimal) -> str | None:
    """Below ~40% → avoid; above ~60% → prefer; else neutral (skip)."""
    if win_rate < (Decimal("0.5") - WIN_RATE_DEVIATION):
        return "avoid"
    if win_rate > (Decimal("0.5") + WIN_RATE_DEVIATION):
        return "prefer"
    return None


def _confidence(n: int, win_rate: Decimal | None = None) -> Decimal:
    """Confidence = sample-size curve × effect-size significance.

    Sample curve clamps n into [MIN_N_PER_BUCKET .. 200] → [0.30 .. 0.95].
    Significance is the z-score of `win_rate` against 50% (binomial SE),
    scaled so z ≥ 2.5 counts fully and z ≤ 1 contributes nothing. A 39% win
    rate over 20 trades (z≈1.0) therefore stays below the 0.40 decision gate,
    while 5% over 20 (z≈4) clears it — previously both scored 0.30.
    Below MIN_N → 0 (lesson won't fire).
    """
    if n < MIN_N_PER_BUCKET:
        return Decimal("0")
    capped = min(n, 200)
    span = Decimal(200 - MIN_N_PER_BUCKET)
    progress = Decimal(capped - MIN_N_PER_BUCKET) / span
    base = Decimal("0.30") + progress * Decimal("0.65")
    if win_rate is None:
        return base.quantize(Decimal("0.0001"))
    se = (Decimal("0.25") / Decimal(n)).sqrt()
    z = abs(Decimal(win_rate) - Decimal("0.5")) / se if se > 0 else Decimal("0")
    sig = max(Decimal("0"), min(Decimal("1"), (z - Decimal("1")) / Decimal("1.5")))
    return (base * sig).quantize(Decimal("0.0001"))


async def _symbol_side_buckets(
    strategy_id: str,
    *,
    asset_class: str,
    since: datetime,
    until: datetime,
) -> list[_PatternStat]:
    """Aggregate by (symbol, side) — the simplest, most reliable pattern.
    Reads outcomes joined to predictions; matrix_agent's hold-only-on-
    horizon model means each prediction has at most one outcome row.

    The asset_class filter keeps crypto and BIST decisions in separate
    buckets — a "long on THYAO" lesson must never inform a crypto
    decision (and vice versa).
    """
    async with shared_session_scope() as session:
        stmt = (
            select(
                Prediction.symbol,
                Prediction.side,
                func.count(Outcome.id).label("n"),
                func.sum(case((Outcome.pnl_usd > 0, 1), else_=0)).label("wins"),
                func.sum(Outcome.pnl_usd).label("total_pnl"),
            )
            .join(Outcome, Outcome.prediction_id == Prediction.id)
            .where(Prediction.strategy_id == strategy_id)
            .where(Prediction.asset_class == asset_class)
            .where(Outcome.observed_at >= since)
            .where(Outcome.observed_at <= until)
            .where(Prediction.side.in_(("long", "short")))
            .group_by(Prediction.symbol, Prediction.side)
        )
        rows = (await session.execute(stmt)).all()

    out: list[_PatternStat] = []
    for r in rows:
        n = int(r.n)
        if n == 0:
            continue
        wins = int(r.wins or 0)
        total = Decimal(r.total_pnl or 0)
        win_rate = (Decimal(wins) / Decimal(n)).quantize(Decimal("0.000001"))
        avg = (total / Decimal(n)).quantize(Decimal("0.000001"))
        out.append(_PatternStat(
            bucket_key=f"{r.symbol}/{r.side}",
            bucket_filter={"symbol": r.symbol, "side": r.side},
            description=f"{r.side} on {r.symbol} (rolling 7d)",
            n=n, wins=wins, total_pnl=total, win_rate=win_rate, avg_pnl=avg,
        ))
    return out


async def _regime_side_buckets(
    strategy_id: str, *, asset_class: str, since: datetime, until: datetime,
) -> list[_PatternStat]:
    """Aggregate by (regime, side) using predictions.context.regime. Answers
    "does this strategy lose when the market is high-vol and falling?" —
    something symbol buckets cannot see."""
    from sqlalchemy import text as _text
    async with shared_session_scope() as session:
        rows = (await session.execute(_text(
            "SELECT p.context->>'regime' AS regime, p.side, count(o.id) AS n, "
            "       sum(CASE WHEN o.pnl_usd > 0 THEN 1 ELSE 0 END) AS wins, sum(o.pnl_usd) AS total_pnl "
            "FROM predictions p JOIN outcomes o ON o.prediction_id = p.id "
            "WHERE p.strategy_id = :sid AND p.asset_class = :ac "
            "  AND o.observed_at >= :since AND o.observed_at <= :until "
            "  AND p.side IN ('long','short') AND p.context->>'regime' IS NOT NULL "
            "  AND p.context->>'regime' <> 'unknown' AND o.reason <> 'orphan_flat_close' "
            "GROUP BY 1, 2"
        ), {"sid": strategy_id, "ac": asset_class, "since": since, "until": until})).all()
    out: list[_PatternStat] = []
    for regime, side, n, wins, total in rows:
        n = int(n or 0)
        if n == 0:
            continue
        wins = int(wins or 0)
        total = Decimal(total or 0)
        wr = (Decimal(wins) / Decimal(n)).quantize(Decimal("0.000001"))
        out.append(_PatternStat(
            bucket_key=f"{regime}/{side}", bucket_filter={"regime": regime, "side": side},
            description=f"{side} in regime {regime} (rolling 7d)",
            n=n, wins=wins, total_pnl=total, win_rate=wr, avg_pnl=(total / Decimal(n)).quantize(Decimal("0.000001")),
        ))
    return out


async def synthesize(
    strategy_id: str = DEFAULT_STRATEGY_ID,
    *,
    asset_class: str = "crypto",
    lookback_hours: int = LOOKBACK_HOURS,
    now: datetime | None = None,
) -> int:
    """Run one synthesis pass for one (strategy_id, asset_class) pair.

    Returns count of NEW lessons written. Idempotent: re-running on the
    same data writes one row per pattern only when win_rate diverged
    from any existing active lesson for the same (strategy, market,
    bucket) beyond a 5pp band. The existing active lesson, if any,
    gets marked superseded and pointed at the new row.
    """
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(hours=lookback_hours)

    # Resolve current strategy_version once — we tag lessons with the
    # version they were aggregated under so a major mutation doesn't
    # poison its successor with stale wisdom.
    from sqlalchemy import select as _select
    from matrix_shared.models import StrategyConfig
    async with shared_session_scope() as session:
        cfg = (await session.execute(
            _select(StrategyConfig)
            .where(StrategyConfig.strategy_id == strategy_id)
            .where(StrategyConfig.status == "active")
            .order_by(desc(StrategyConfig.version))
            .limit(1)
        )).scalar_one_or_none()
    if cfg is None:
        logger.warning(
            f"synthesize: no active strategy_config for {strategy_id}"
        )
        return 0
    version = int(cfg.version)

    written = 0
    buckets: list[tuple[str, list[_PatternStat]]] = [
        ("symbol_specific", await _symbol_side_buckets(strategy_id, asset_class=asset_class, since=since, until=now)),
        ("regime", await _regime_side_buckets(strategy_id, asset_class=asset_class, since=since, until=now)),
    ]
    for kind, stats in buckets:
        for s in stats:
            if s.n < MIN_N_PER_BUCKET:
                continue
            verdict = _verdict_for(s.win_rate)
            if verdict is None:
                # Neutral — supersede any active lesson for this bucket so we
                # don't leave stale advice live when the pattern washes out.
                existing = await _find_active(strategy_id, asset_class, kind, s.bucket_filter)
                if existing is not None and not str(existing.pattern_description or "").startswith("OPERATOR:"):
                    await _supersede_if_active(strategy_id, asset_class, kind, s.bucket_filter, now)
                continue

            # Has an active lesson for this exact bucket already? If its
            # win_rate matches within 5pp, keep it. Otherwise supersede + write
            # a fresh one.
            existing = await _find_active(strategy_id, asset_class, kind, s.bucket_filter)
            if existing is not None and str(existing.pattern_description or "").startswith("OPERATOR:"):
                continue  # operator directives are not statistical; never overwrite them
            if existing is not None and existing.win_rate is not None:
                if abs(Decimal(existing.win_rate) - s.win_rate) < Decimal("0.05"):
                    # Close enough — keep it, but record that fresh outcomes still
                    # confirm the pattern so the TTL sweep doesn't expire it.
                    await _touch_confirmed(existing.id, s.n, now)
                    continue
            new_id = await _insert_lesson(
                strategy_id=strategy_id, asset_class=asset_class, version=version, kind=kind,
                stat=s, verdict=verdict, observed_from=since, observed_until=now,
            )
            if existing is not None:
                await _mark_superseded(existing.id, new_id, now)
            written += 1
            logger.info(
                f"lesson {new_id} [{asset_class}/{kind}] ({verdict}): {s.description} "
                f"n={s.n} win={float(s.win_rate)*100:.1f}% pnl=${float(s.total_pnl):.2f}"
            )
    return written


async def _find_active(
    strategy_id: str,
    asset_class: str,
    kind: str,
    bucket_filter: dict[str, Any],
) -> AgentLesson | None:
    async with shared_session_scope() as session:
        rows = (await session.execute(
            select(AgentLesson)
            .where(AgentLesson.strategy_id == strategy_id)
            .where(AgentLesson.asset_class == asset_class)
            .where(AgentLesson.status == "active")
            .where(AgentLesson.pattern_kind == kind)
        )).scalars().all()
        for r in rows:
            if r.pattern_filter == bucket_filter:
                session.expunge(r)
                return r
    return None


async def _insert_lesson(
    *,
    strategy_id: str,
    asset_class: str,
    version: int,
    kind: str,
    stat: _PatternStat,
    verdict: str,
    observed_from: datetime,
    observed_until: datetime,
) -> uuid.UUID:
    new_id = uuid.uuid4()
    async with shared_session_scope() as session:
        session.add(AgentLesson(
            id=new_id,
            strategy_id=strategy_id,
            asset_class=asset_class,
            strategy_version=version,
            pattern_kind=kind,
            pattern_description=stat.description,
            pattern_filter=stat.bucket_filter,
            n_observations=stat.n,
            win_rate=stat.win_rate,
            avg_pnl_usd=stat.avg_pnl,
            total_pnl_usd=stat.total_pnl,
            verdict=verdict,
            confidence=_confidence(stat.n, stat.win_rate),
            observed_from=observed_from,
            observed_until=observed_until,
            generated_at=datetime.now(timezone.utc),
            status="active",
        ))
    # Overlay: Lesson node + GENERALIZED_INTO edges from the bucket's outcomes.
    from matrix_shared.graph_overlay import upsert_lesson_node

    await upsert_lesson_node(
        lesson_id=str(new_id), pattern_kind=kind, verdict=verdict,
        confidence=str(_confidence(stat.n, stat.win_rate)), description=stat.description,
        symbol=stat.bucket_filter.get("symbol"), side=stat.bucket_filter.get("side"),
        strategy_id=strategy_id,
    )
    return new_id


async def _mark_superseded(
    old_id: uuid.UUID, new_id: uuid.UUID, at: datetime
) -> None:
    async with shared_session_scope() as session:
        await session.execute(
            update(AgentLesson)
            .where(AgentLesson.id == old_id)
            .values(status="superseded", superseded_by=new_id, updated_at=at)
        )
    from matrix_shared.graph_overlay import set_lesson_status

    await set_lesson_status(lesson_id=str(old_id), status="superseded")


async def _supersede_if_active(
    strategy_id: str,
    asset_class: str,
    kind: str,
    bucket_filter: dict[str, Any],
    at: datetime,
) -> None:
    """Lesson's pattern is no longer informative — flip to superseded with
    no replacement. Caller computed neutral verdict."""
    existing = await _find_active(strategy_id, asset_class, kind, bucket_filter)
    if existing is None:
        return
    async with shared_session_scope() as session:
        await session.execute(
            update(AgentLesson)
            .where(AgentLesson.id == existing.id)
            .values(status="expired", updated_at=at)
        )
    from matrix_shared.graph_overlay import set_lesson_status

    await set_lesson_status(lesson_id=str(existing.id), status="expired")
    logger.info(f"lesson {existing.id} expired (pattern returned to neutral)")


async def _touch_confirmed(lesson_id: uuid.UUID, n: int, at: datetime) -> None:
    async with shared_session_scope() as session:
        await session.execute(
            update(AgentLesson)
            .where(AgentLesson.id == lesson_id)
            .values(observed_until=at, n_observations=n, updated_at=at)
        )


async def expire_stale_lessons(*, now: datetime | None = None, ttl_days: int = LESSON_TTL_DAYS) -> int:
    """Active lessons not re-confirmed for `ttl_days` → expired.

    Closes the self-locking loop: an `avoid` lesson suppresses the very
    trades that could refresh it, so without a TTL a false positive lived
    forever.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=ttl_days)
    async with shared_session_scope() as session:
        result = await session.execute(
            update(AgentLesson)
            .where(AgentLesson.status == "active")
            .where(AgentLesson.observed_until < cutoff)
            .values(status="expired", updated_at=now)
            .returning(AgentLesson.id)
        )
        ids = list(result.scalars())
    if ids:
        from matrix_shared.graph_overlay import set_lesson_status

        for lid in ids:
            await set_lesson_status(lesson_id=str(lid), status="expired")
        logger.info(f"expired {len(ids)} lesson(s) not re-confirmed within {ttl_days}d")
    return len(ids)


async def retire_contradicted_lessons(*, min_n: int = BYPASS_MIN_N) -> int:
    """Lesson efficacy from the exploration corridor: trades that bypassed an
    `avoid` lesson (context.lesson_bypass = lesson id) are the counterfactual.
    If ≥ min_n of them were scored and they were profitable with a ≥ 50% win
    rate, the lesson is wrong → expired."""
    from sqlalchemy import text as _text
    async with shared_session_scope() as session:
        rows = (await session.execute(_text(
            "SELECT l.id, count(o.id) AS n, "
            "       avg(CASE WHEN o.pnl_usd > 0 THEN 1.0 ELSE 0.0 END) AS wr, "
            "       sum(o.pnl_usd) AS pnl "
            "FROM agent_lessons l "
            "JOIN predictions p ON p.context->>'lesson_bypass' = l.id::text "
            "JOIN outcomes o ON o.prediction_id = p.id "
            "WHERE l.status = 'active' AND l.verdict = 'avoid' "
            "  AND l.pattern_description NOT LIKE 'OPERATOR:%' "
            "GROUP BY l.id HAVING count(o.id) >= :min_n"
        ), {"min_n": min_n})).all()
        retired = 0
        for lid, n, wr, pnl in rows:
            if wr is not None and Decimal(wr) >= Decimal("0.5") and Decimal(pnl or 0) > 0:
                await session.execute(
                    update(AgentLesson).where(AgentLesson.id == lid)
                    .values(status="expired", updated_at=datetime.now(timezone.utc))
                )
                retired += 1
                logger.info(
                    f"lesson {lid} retired: {n} corridor trades contradicted it "
                    f"(win {float(wr)*100:.0f}%, pnl ${float(pnl):.2f})"
                )
    return retired

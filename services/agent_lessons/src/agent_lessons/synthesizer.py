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

Only `symbol_specific` is implemented in v0 because it needs no
feature-extraction at lesson-write time — the prediction row already
carries symbol + side directly. `feature_value_band` patterns will land
once predictions.context (already JSON) is reliably populated by the
live agent's decide() path; until then this synthesizer would be
guessing on stale features.
"""

from __future__ import annotations

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


def _confidence(n: int) -> Decimal:
    """Soft confidence curve: clamps n into [MIN_N_PER_BUCKET .. 200] →
    [0.30 .. 0.95]. Below MIN_N → 0 (lesson won't fire)."""
    if n < MIN_N_PER_BUCKET:
        return Decimal("0")
    capped = min(n, 200)
    span = Decimal(200 - MIN_N_PER_BUCKET)
    progress = Decimal(capped - MIN_N_PER_BUCKET) / span
    return (Decimal("0.30") + progress * Decimal("0.65")).quantize(Decimal("0.0001"))


async def _symbol_side_buckets(
    strategy_id: str, *, since: datetime, until: datetime
) -> list[_PatternStat]:
    """Aggregate by (symbol, side) — the simplest, most reliable pattern.
    Reads outcomes joined to predictions; matrix_agent's hold-only-on-
    horizon model means each prediction has at most one outcome row."""
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


async def synthesize(
    strategy_id: str = DEFAULT_STRATEGY_ID,
    *,
    lookback_hours: int = LOOKBACK_HOURS,
    now: datetime | None = None,
) -> int:
    """Run one synthesis pass. Returns count of NEW lessons written.

    Idempotent: re-running on the same data writes one row per pattern
    only when win_rate diverged from any existing active lesson for the
    same bucket beyond a 5pp band. The existing active lesson, if any,
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
        logger.warning(f"synthesize: no active strategy_config for {strategy_id}")
        return 0
    version = int(cfg.version)

    stats = await _symbol_side_buckets(strategy_id, since=since, until=now)
    written = 0

    for s in stats:
        if s.n < MIN_N_PER_BUCKET:
            continue
        verdict = _verdict_for(s.win_rate)
        if verdict is None:
            # Neutral — supersede any active lesson for this bucket so we
            # don't leave stale advice live when the pattern washes out.
            await _supersede_if_active(strategy_id, "symbol_specific", s.bucket_filter, now)
            continue

        # Has an active lesson for this exact bucket already? If its
        # win_rate matches within 5pp, keep it. Otherwise supersede + write
        # a fresh one.
        existing = await _find_active(strategy_id, "symbol_specific", s.bucket_filter)
        if existing is not None and existing.win_rate is not None:
            if abs(Decimal(existing.win_rate) - s.win_rate) < Decimal("0.05"):
                continue  # close enough — leave it
        new_id = await _insert_lesson(
            strategy_id=strategy_id,
            version=version,
            kind="symbol_specific",
            stat=s,
            verdict=verdict,
            observed_from=since,
            observed_until=now,
        )
        if existing is not None:
            await _mark_superseded(existing.id, new_id, now)
        written += 1
        logger.info(
            f"lesson {new_id} ({verdict}): {s.description} "
            f"n={s.n} win={float(s.win_rate)*100:.1f}% pnl=${float(s.total_pnl):.2f}"
        )
    return written


async def _find_active(
    strategy_id: str, kind: str, bucket_filter: dict[str, Any]
) -> AgentLesson | None:
    async with shared_session_scope() as session:
        rows = (await session.execute(
            select(AgentLesson)
            .where(AgentLesson.strategy_id == strategy_id)
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
            strategy_version=version,
            pattern_kind=kind,
            pattern_description=stat.description,
            pattern_filter=stat.bucket_filter,
            n_observations=stat.n,
            win_rate=stat.win_rate,
            avg_pnl_usd=stat.avg_pnl,
            total_pnl_usd=stat.total_pnl,
            verdict=verdict,
            confidence=_confidence(stat.n),
            observed_from=observed_from,
            observed_until=observed_until,
            generated_at=datetime.now(timezone.utc),
            status="active",
        ))
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


async def _supersede_if_active(
    strategy_id: str,
    kind: str,
    bucket_filter: dict[str, Any],
    at: datetime,
) -> None:
    """Lesson's pattern is no longer informative — flip to superseded with
    no replacement. Caller computed neutral verdict."""
    existing = await _find_active(strategy_id, kind, bucket_filter)
    if existing is None:
        return
    async with shared_session_scope() as session:
        await session.execute(
            update(AgentLesson)
            .where(AgentLesson.id == existing.id)
            .values(status="expired", updated_at=at)
        )
    logger.info(f"lesson {existing.id} expired (pattern returned to neutral)")

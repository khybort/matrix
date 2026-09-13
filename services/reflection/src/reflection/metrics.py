"""Aggregate outcome metrics per strategy over a time window."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from matrix_shared import shared_session_scope
from matrix_shared.models import Outcome, Prediction
from sqlalchemy import case, func, select


@dataclass(slots=True)
class StrategyMetrics:
    strategy_id: str
    version: int
    n_outcomes: int
    avg_score: Decimal
    win_rate: Decimal  # share of outcomes with positive pnl_usd
    total_pnl_usd: Decimal
    by_symbol: dict[str, dict[str, str]]  # symbol → {n, avg_score, ...}
    asset_class: str | None = None  # None = unscoped (legacy callers)
    # exit reason → {n, avg_pnl_bps, total_pnl_usd}. The geometry signal the
    # tuner needs: hit_horizon dominating at ≈ −cost means TP is out of reach
    # for the horizon; hit_sl:hit_tp well above the TP/SL ratio means the stop
    # is inside the noise band (2026-09-13: SL:TP 2-3:1 across all champions).
    by_reason: dict[str, dict[str, str]] = field(default_factory=dict)


async def metrics_window(
    strategy_id: str,
    version: int,
    *,
    asset_class: str | None = None,
    window_hours: float = 24.0,
) -> StrategyMetrics:
    """Aggregate outcomes for (strategy_id, version[, asset_class]).

    Always pass `asset_class` from the StrategyConfig row being reflected on:
    `matrix_agent` exists in crypto AND bist, and BIST strategies must never
    be scored (or mutated) against crypto rows.
    """
    since = datetime.now(UTC) - timedelta(hours=window_hours)
    async with shared_session_scope() as session:
        # Aggregate
        agg_stmt = (
            select(
                func.count(Outcome.id),
                func.avg(Outcome.score),
                func.sum(Outcome.pnl_usd),
                func.sum(case((Outcome.pnl_usd > 0, 1), else_=0)),
            )
            .join(Prediction, Prediction.id == Outcome.prediction_id)
            .where(Prediction.strategy_id == strategy_id)
            .where(Prediction.strategy_version == version)
            .where(Outcome.observed_at >= since)
            .where(Outcome.reason != "orphan_flat_close")
        )
        if asset_class is not None:
            agg_stmt = agg_stmt.where(Prediction.asset_class == asset_class)
        n, avg, total, wins = (await session.execute(agg_stmt)).one()
        n = int(n or 0)
        avg = Decimal(avg) if avg is not None else Decimal("0")
        total = Decimal(total) if total is not None else Decimal("0")
        wins = int(wins or 0)
        win_rate = Decimal(wins) / Decimal(n) if n else Decimal("0")

        # By symbol
        sym_stmt = (
            select(
                Prediction.symbol,
                func.count(Outcome.id),
                func.avg(Outcome.score),
                func.sum(Outcome.pnl_usd),
            )
            .join(Prediction, Prediction.id == Outcome.prediction_id)
            .where(Prediction.strategy_id == strategy_id)
            .where(Prediction.strategy_version == version)
            .where(Outcome.observed_at >= since)
            .where(Outcome.reason != "orphan_flat_close")
            .group_by(Prediction.symbol)
        )
        if asset_class is not None:
            sym_stmt = sym_stmt.where(Prediction.asset_class == asset_class)
        by_symbol: dict[str, dict[str, str]] = {}
        for sym, count, sym_avg, sym_total in (await session.execute(sym_stmt)).all():
            by_symbol[sym] = {
                "n": str(count),
                "avg_score": str(Decimal(sym_avg) if sym_avg is not None else 0),
                "total_pnl_usd": str(Decimal(sym_total) if sym_total is not None else 0),
            }

        # By exit reason
        reason_stmt = (
            select(Outcome.reason, func.count(Outcome.id), func.avg(Outcome.pnl_pct), func.sum(Outcome.pnl_usd))
            .join(Prediction, Prediction.id == Outcome.prediction_id)
            .where(Prediction.strategy_id == strategy_id)
            .where(Prediction.strategy_version == version)
            .where(Outcome.observed_at >= since)
            .where(Outcome.reason != "orphan_flat_close")
            .group_by(Outcome.reason)
        )
        if asset_class is not None:
            reason_stmt = reason_stmt.where(Prediction.asset_class == asset_class)
        by_reason: dict[str, dict[str, str]] = {}
        for reason, count, avg_pct, r_total in (await session.execute(reason_stmt)).all():
            by_reason[str(reason)] = {
                "n": str(count),
                "avg_pnl_bps": str((Decimal(avg_pct) * 10000).quantize(Decimal("0.1")) if avg_pct is not None else 0),
                "total_pnl_usd": str(Decimal(r_total) if r_total is not None else 0),
            }

    return StrategyMetrics(
        strategy_id=strategy_id,
        version=version,
        n_outcomes=n,
        avg_score=avg,
        win_rate=win_rate,
        total_pnl_usd=total,
        by_symbol=by_symbol,
        asset_class=asset_class,
        by_reason=by_reason,
    )

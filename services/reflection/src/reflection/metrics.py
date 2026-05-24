"""Aggregate outcome metrics per strategy over a time window."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import case, func, select

from matrix_shared import shared_session_scope
from matrix_shared.models import Outcome, Prediction


@dataclass(slots=True)
class StrategyMetrics:
    strategy_id: str
    version: int
    n_outcomes: int
    avg_score: Decimal
    win_rate: Decimal  # share of positive scores
    total_pnl_usd: Decimal
    by_symbol: dict[str, dict[str, str]]  # symbol → {n, avg_score, ...}


async def metrics_window(
    strategy_id: str, version: int, *, window_hours: float = 24.0
) -> StrategyMetrics:
    since = datetime.now(UTC) - timedelta(hours=window_hours)
    async with shared_session_scope() as session:
        # Aggregate
        agg_stmt = (
            select(
                func.count(Outcome.id),
                func.avg(Outcome.score),
                func.sum(Outcome.pnl_usd),
                func.sum(case((Outcome.score > 0, 1), else_=0)),
            )
            .join(Prediction, Prediction.id == Outcome.prediction_id)
            .where(Prediction.strategy_id == strategy_id)
            .where(Prediction.strategy_version == version)
            .where(Outcome.observed_at >= since)
        )
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
            .group_by(Prediction.symbol)
        )
        by_symbol: dict[str, dict[str, str]] = {}
        for sym, count, sym_avg, sym_total in (await session.execute(sym_stmt)).all():
            by_symbol[sym] = {
                "n": str(count),
                "avg_score": str(Decimal(sym_avg) if sym_avg is not None else 0),
                "total_pnl_usd": str(Decimal(sym_total) if sym_total is not None else 0),
            }

    return StrategyMetrics(
        strategy_id=strategy_id,
        version=version,
        n_outcomes=n,
        avg_score=avg,
        win_rate=win_rate,
        total_pnl_usd=total,
        by_symbol=by_symbol,
    )

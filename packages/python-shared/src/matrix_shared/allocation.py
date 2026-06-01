"""Capital allocation helpers: strategy×symbol fit and dynamic risk sizing.

Used by the paper-trade gate to rank candidates and scale notionals so
profitable pairings get more slots/size while cold streaks shrink exposure.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from matrix_shared.models import PaperPosition, Prediction

PAIR_LOOKBACK_DAYS = 14
EDGE_SHRINK_K = 5
PNL_PCT_SCALE = 0.02  # 2% avg return → top of clamp range


def shrink_pair_edge(n: int, avg_pnl_pct: float) -> float:
    """Map realized return into [0, 1] with Bayesian shrinkage toward neutral."""
    if n <= 0:
        return 0.5
    raw = max(-1.0, min(1.0, avg_pnl_pct / PNL_PCT_SCALE))
    shrunk = (n * raw) / (n + EDGE_SHRINK_K)
    return max(0.0, min(1.0, (shrunk + 1.0) / 2.0))


def edge_multiplier(score: float | None, *, neutral: float = 1.0) -> float:
    """Turn a [0,1] edge score into an EV multiplier in [0.5, 1.5]."""
    if score is None:
        return neutral
    return max(0.5, min(1.5, 0.5 + score))


def expected_value(
    *,
    confidence: float,
    tp_pct: float,
    sl_pct: float,
    symbol_edge: float | None = None,
    strategy_perf: float | None = None,
    pair_edge: float | None = None,
) -> float:
    """Risk-adjusted expected value for ranking open predictions."""
    base_ev = confidence * tp_pct - (1.0 - confidence) * sl_pct
    return (
        base_ev
        * edge_multiplier(symbol_edge)
        * edge_multiplier(strategy_perf)
        * edge_multiplier(pair_edge)
    )


def risk_multiplier(
    *,
    confidence: Decimal,
    perf_score: float | None = None,
    pair_edge: float | None = None,
    consecutive_losses: int = 0,
) -> Decimal:
    """Scale notional by confidence, learned fit, and recent drawdown."""
    risk = confidence
    if perf_score is not None:
        risk *= Decimal(str(max(0.25, min(1.25, perf_score))))
    if pair_edge is not None:
        risk *= Decimal(str(max(0.35, min(1.25, 0.35 + pair_edge))))
    if consecutive_losses >= 8:
        risk *= Decimal("0.25")
    elif consecutive_losses >= 3:
        risk *= Decimal("0.5")
    return max(Decimal("0.05"), min(Decimal("1.0"), risk))


async def load_pair_edges(
    session: AsyncSession,
    *,
    wallet_id: uuid.UUID,
    strategy_ids: set[str],
    symbols: set[str],
    lookback_days: int = PAIR_LOOKBACK_DAYS,
) -> dict[tuple[str, str], float]:
    """Realized edge per (strategy_id, symbol) from closed paper positions."""
    if not strategy_ids or not symbols:
        return {}

    since = datetime.now(UTC) - timedelta(days=lookback_days)
    rows = (
        await session.execute(
            select(
                Prediction.strategy_id,
                PaperPosition.symbol,
                func.count(PaperPosition.id).label("n"),
                func.avg(
                    PaperPosition.pnl_usd / func.nullif(PaperPosition.notional_usd, 0)
                ).label("avg_pnl_pct"),
            )
            .join(Prediction, Prediction.id == PaperPosition.prediction_id)
            .where(PaperPosition.wallet_id == wallet_id)
            .where(PaperPosition.status == "closed")
            .where(PaperPosition.closed_at >= since)
            .where(Prediction.strategy_id.in_(strategy_ids))
            .where(PaperPosition.symbol.in_(symbols))
            .group_by(Prediction.strategy_id, PaperPosition.symbol)
        )
    ).all()

    out: dict[tuple[str, str], float] = {}
    for row in rows:
        n = int(row.n or 0)
        if n <= 0:
            continue
        out[(row.strategy_id, row.symbol)] = shrink_pair_edge(
            n, float(row.avg_pnl_pct or 0.0)
        )
    return out

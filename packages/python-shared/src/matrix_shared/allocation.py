"""Capital allocation helpers: strategy×symbol fit and dynamic risk sizing.

Used by the paper-trade gate to rank candidates and scale notionals so
profitable pairings get more slots/size while cold streaks shrink exposure.
"""

from __future__ import annotations

import math
import os
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from matrix_shared.edge_study import episode_groups
from matrix_shared.models import PaperPosition, Prediction

PAIR_LOOKBACK_DAYS = 14
EDGE_SHRINK_K = 5
PNL_PCT_SCALE = 0.02  # 2% avg return → top of clamp range

# --- Kelly sizing on a measured edge -----------------------------------------
# Applied only to a strategy whose edge survived both nulls in `edge_study`.
# Everything unproven keeps the old confidence/streak sizing untouched.
KELLY_FRACTION = float(os.environ.get("MATRIX_KELLY_FRACTION", "0.25"))  # quarter-Kelly
KELLY_PRIOR_N = float(os.environ.get("MATRIX_KELLY_PRIOR_N", "50"))
KELLY_MAX_F = float(os.environ.get("MATRIX_KELLY_MAX_F", "0.05"))  # ≤5% of equity


def kelly_fraction_of_equity(
    *,
    edge_bps: float,
    sd_bps: float,
    n: int,
    cost_bps: float,
    t_stat: float | None = None,
    concurrency: int = 1,
    fraction: float = KELLY_FRACTION,
    prior_n: float = KELLY_PRIOR_N,
) -> float | None:
    """Fraction of equity one position may take, given a measured per-trade edge.

    `f* = mu / sigma^2` is the Kelly fraction for a continuous return. Applied
    naively to a bracket trade it returns multiples of equity — per-trade
    variance is small next to equity, so the arithmetic asks for leverage that
    only makes sense for one bet at a time held to resolution. Four corrections
    make it usable:

    - **Costs first.** An edge smaller than the round trip returns 0.0 (size
      nothing) rather than None, which means "no opinion".
    - **The lower confidence bound, not the point estimate.** With a t-stat we
      size on `edge - 1.96*se`; an edge of 31 bps at t=5 is really "at least
      ~19 bps". Without one, shrink by `n / (n + prior_n)` instead. Kelly on an
      over-estimated edge is the classic way to go broke while being right.
    - **Divide by concurrency.** The formula assumes the bet is the book. We
      hold up to `max_concurrent_positions` at once, and crypto perps move
      together, so the full fraction cannot go into each one.
    - **A hard cap** (`KELLY_MAX_F`) on top, because `sd` is itself estimated
      and a small one must not produce a large position.

    Returns None when the inputs cannot support a decision, so the caller keeps
    its existing sizing instead of reading it as "size zero".
    """
    if n <= 0 or sd_bps <= 0.0:
        return None
    if t_stat is not None and t_stat > 0:
        se = abs(edge_bps) / t_stat
        edge_used = max(0.0, edge_bps - 1.96 * se)
    else:
        edge_used = edge_bps * (n / (n + prior_n))
    net = edge_used - cost_bps
    if net <= 0.0:
        return 0.0
    mu = net / 10_000.0
    sigma = sd_bps / 10_000.0
    f = fraction * mu / (sigma * sigma) / max(1, concurrency)
    if not math.isfinite(f):
        return None
    return max(0.0, min(KELLY_MAX_F, f))


def kelly_notional(
    *,
    equity: Decimal,
    max_notional: Decimal,
    kelly_f: float | None,
) -> Decimal | None:
    """Kelly size, floored at nothing and capped by the wallet's risk gate.

    The gate is the ceiling in every case: Kelly may size *below*
    `max_position_pct`, never above it. That keeps a single measurement error
    from widening a hard risk limit."""
    if kelly_f is None:
        return None
    return min(max_notional, (equity * Decimal(str(kelly_f))).quantize(Decimal("0.01")))


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
    """Scale notional by confidence, learned fit, and recent drawdown.

    `perf_score` is on the canonical [0, 1] scale with 0.5 = neutral (same as
    `edge_multiplier` and `slot_scorer._perf_score`), so it is mapped
    `0.5 + perf_score` into a [0.25, 1.25] notional multiplier: neutral → 1.0×,
    a proven strategy → up to 1.25×, a chronic loser → 0.25×. (Before 2026-09-15
    this used `perf_score` directly as the multiplier, which — combined with the
    old signed [-0.6, 1.0] score — sized every neutral/negative strategy at the
    0.25× floor.)
    """
    risk = confidence
    if perf_score is not None:
        risk *= Decimal(str(max(0.25, min(1.25, 0.5 + perf_score))))
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
    """Realized edge per (strategy_id, symbol) from closed paper positions,
    one sample per episode.

    A bet re-filled while its twin was still open is one call, not several
    (`edge_study.episode_groups`): per row, one lucky re-filled call escaped
    the shrinkage as if it were n agreeing trades. Each episode's return is
    its dollars over its notional.
    """
    if not strategy_ids or not symbols:
        return {}

    since = datetime.now(UTC) - timedelta(days=lookback_days)
    rows = (
        await session.execute(
            select(
                Prediction.strategy_id,
                Prediction.asset_class,
                Prediction.side,
                Prediction.generated_at,
                Prediction.horizon_seconds,
                PaperPosition.symbol,
                PaperPosition.pnl_usd,
                PaperPosition.notional_usd,
            )
            .join(Prediction, Prediction.id == PaperPosition.prediction_id)
            .where(PaperPosition.wallet_id == wallet_id)
            .where(PaperPosition.status == "closed")
            .where(PaperPosition.closed_at >= since)
            .where(Prediction.strategy_id.in_(strategy_ids))
            .where(PaperPosition.symbol.in_(symbols))
        )
    ).mappings().all()
    return pair_episode_edges([dict(r) for r in rows])


def pair_episode_edges(rows: list[dict]) -> dict[tuple[str, str], float]:
    """Shrunk edge per (strategy_id, symbol); n is the episode count."""
    per_pair: dict[tuple[str, str], list[float]] = {}
    for g in episode_groups(sorted(rows, key=lambda r: r["generated_at"])):
        notional = sum(float(r["notional_usd"] or 0) for r in g)
        if notional <= 0:
            continue
        pnl = sum(float(r["pnl_usd"] or 0) for r in g)
        per_pair.setdefault((g[0]["strategy_id"], g[0]["symbol"]), []).append(pnl / notional)
    return {k: shrink_pair_edge(len(xs), sum(xs) / len(xs)) for k, xs in per_pair.items()}

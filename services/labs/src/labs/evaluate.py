"""Evaluation loop:
    - For each active experiment × symbol, generate a hypothetical decision.
    - On non-hold: insert LabEvaluation(status='open') with entry_price, close_at.
    - For evaluations whose close_at has passed: fetch the latest market price
      at-or-near close_at, compute pnl_pct + score, update fitness aggregates.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from agent.features import extract_symbol_features
from loguru import logger
from sqlalchemy import desc, select

from matrix_shared import session_scope
from matrix_shared.models import LabEvaluation, LabExperiment, MarketTrade

from labs.decide import decide_with_genome
from labs.genome import Genome

# Cap on per-trade pnl for score normalization; ±1% maps to ±1
SCORE_CAP_PCT = Decimal("0.01")
# How fresh "now" price must be to open a new evaluation
ENTRY_FRESHNESS_S = 30
# Window around close_at to accept a mark price (need a trade within this window)
MARK_WINDOW_S = 60


async def emit_signals(symbols: list[str]) -> int:
    """For each active experiment × symbol, emit at most one fresh evaluation."""
    async with session_scope() as session:
        stmt = (
            select(LabExperiment)
            .where(LabExperiment.status == "active")
            .order_by(LabExperiment.created_at.asc())
        )
        experiments = list((await session.execute(stmt)).scalars())

    if not experiments:
        return 0

    # Pre-extract features once per symbol (cheap one-shot SQL each)
    feature_cache = {sym: await extract_symbol_features(sym) for sym in symbols}

    opened = 0
    now = datetime.now(UTC)
    for exp in experiments:
        try:
            genome = Genome.from_dict(exp.params)
        except Exception as e:
            logger.warning(f"experiment {exp.id}: bad params, skipping: {e}")
            continue

        for sym in symbols:
            f = feature_cache[sym]
            if f.last_price is None:
                continue
            d = decide_with_genome(genome, f)
            if d.side == "hold" or d.confidence < Decimal("0.05"):
                continue

            close_at = now + timedelta(seconds=genome.horizon_seconds)
            async with session_scope() as session:
                session.add(
                    LabEvaluation(
                        experiment_id=exp.id,
                        symbol=sym,
                        side=d.side,
                        confidence=d.confidence,
                        generated_at=now,
                        close_at=close_at,
                        entry_price=d.last_price,
                        status="open",
                    )
                )
                exp_db = await session.get(LabExperiment, exp.id)
                if exp_db is not None:
                    exp_db.n_signals += 1
            opened += 1

    return opened


async def _mark_price_near(symbol: str, target_ts: datetime) -> Decimal | None:
    """Find a trade price within MARK_WINDOW_S of target_ts (closest)."""
    window = timedelta(seconds=MARK_WINDOW_S)
    lo = target_ts - window
    hi = target_ts + window
    async with session_scope() as session:
        stmt = (
            select(MarketTrade.price, MarketTrade.trade_ts)
            .where(MarketTrade.symbol == symbol)
            .where(MarketTrade.trade_ts >= lo)
            .where(MarketTrade.trade_ts <= hi)
            .order_by(MarketTrade.trade_ts.asc())
        )
        rows = (await session.execute(stmt)).all()
    if not rows:
        return None
    # nearest by absolute distance
    best = min(rows, key=lambda r: abs((r.trade_ts - target_ts).total_seconds()))
    return Decimal(best.price)


async def score_due_evaluations(stale_after_s: int = 600) -> tuple[int, int]:
    """Score evaluations whose close_at has passed. Returns (scored, stale)."""
    now = datetime.now(UTC)
    cutoff = now - timedelta(seconds=stale_after_s)
    async with session_scope() as session:
        stmt = (
            select(LabEvaluation)
            .where(LabEvaluation.status == "open")
            .where(LabEvaluation.close_at <= now)
            .order_by(LabEvaluation.close_at.asc())
        )
        evals = list((await session.execute(stmt)).scalars())

    scored = 0
    stale = 0
    for ev in evals:
        mark = await _mark_price_near(ev.symbol, ev.close_at)
        if mark is None:
            if ev.close_at < cutoff:
                async with session_scope() as session:
                    ev_db = await session.get(LabEvaluation, ev.id)
                    if ev_db is not None:
                        ev_db.status = "stale"
                stale += 1
            continue

        if ev.side == "long":
            pnl_pct = (mark - ev.entry_price) / ev.entry_price
        else:
            pnl_pct = (ev.entry_price - mark) / ev.entry_price
        capped = max(min(pnl_pct, SCORE_CAP_PCT), -SCORE_CAP_PCT)
        score = capped / SCORE_CAP_PCT  # in [-1, 1]

        async with session_scope() as session:
            ev_db = await session.get(LabEvaluation, ev.id)
            if ev_db is None:
                continue
            ev_db.exit_price = mark
            ev_db.pnl_pct = pnl_pct
            ev_db.score = score
            ev_db.status = "scored"

            exp_db = await session.get(LabExperiment, ev.experiment_id)
            if exp_db is not None:
                exp_db.n_evaluations += 1
                if score > 0:
                    exp_db.n_wins += 1
                exp_db.total_score = exp_db.total_score + score
                # fitness: avg_score * sqrt(min(n, 25)/25), to value sample size
                avg = exp_db.total_score / Decimal(exp_db.n_evaluations)
                from decimal import Decimal as D
                sample_factor = (
                    D(str(min(exp_db.n_evaluations, 25) / 25.0)) ** D("0.5")
                )
                exp_db.fitness_score = avg * sample_factor
        scored += 1

    return scored, stale

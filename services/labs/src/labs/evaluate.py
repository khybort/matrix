"""Evaluation loop:
    - For each active experiment × symbol, generate a hypothetical decision.
    - On non-hold: insert LabEvaluation(status='open') with entry_price, close_at.
    - For evaluations whose close_at has passed: fetch the latest market price
      at-or-near close_at, compute pnl_pct + score, update fitness aggregates.
"""

from __future__ import annotations

import statistics
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from agent.features import PERP_VENUE, extract_symbol_features
from loguru import logger
from sqlalchemy import func, select

from matrix_shared import local_session_scope, shared_session_scope
from matrix_shared.edge_study import one_per_episode
from matrix_shared.models import (
    LabEvaluation,
    LabExperiment,
    MarketBar,
    MarketTrade,
    TickerSnapshot,
)
from matrix_shared.trading import apply_slippage

from labs.decide import decide_with_genome
from labs.genome import Genome

# Cap on per-trade pnl for score normalization; ±1% maps to ±1
SCORE_CAP_PCT = Decimal("0.01")
# How fresh the "now" price must be to open a new evaluation. Until 2026-10-09
# this was declared and never checked: after an ingestion outage the entry was
# the last ticker before the hole and the exit a live trade, so the score was
# the jump across the hole, not anything the genome decided.
ENTRY_FRESHNESS_S = 60
# Window around close_at to accept a mark price (need a trade within this window)
MARK_WINDOW_S = 60
# BIST uses 1m bars + Yahoo's ~15min delay — widen the score window accordingly.
MARK_WINDOW_S_BARS = 60 * 30


FITNESS_K = Decimal(__import__("os").environ.get("MATRIX_LAB_FITNESS_K", "1.0"))
FITNESS_FULL_N = 25


@dataclass(frozen=True, slots=True)
class EpisodeStats:
    n: int  # independent episodes (the sample size)
    n_raw: int  # scored rows
    n_wins: int
    total_score: Decimal
    std: Decimal
    fitness: Decimal


def _episodes(rows: list[dict]) -> list[dict]:
    """The bets among one experiment's scored evaluations, in time order."""
    items = sorted(
        (
            {
                "strategy_id": r["experiment_id"],
                "asset_class": r["asset_class"],
                "symbol": r["symbol"],
                "side": r["side"],
                "generated_at": r["generated_at"],
                "horizon_seconds": max(1, int((r["close_at"] - r["generated_at"]).total_seconds())),
                "score": Decimal(r["score"]),
                "pnl_pct": Decimal(r["pnl_pct"]),
            }
            for r in rows
        ),
        key=lambda r: r["generated_at"],
    )
    return one_per_episode(items)


def episode_stats(rows: list[dict]) -> EpisodeStats:
    """Fitness inputs over episodes, not rows.

    `rows` are scored evaluations of ONE experiment (experiment_id, asset_class,
    symbol, side, generated_at, close_at, score, pnl_pct), any order. Before
    2026-09-13 the 20 s tick opened a new evaluation per (experiment, symbol)
    every tick while one was still open, so 88 % of all lab rows were copies of
    a bet already being scored; the counters summed them as samples (one
    promoted genome: 129 wins of 152). The episode definition is the edge
    study's (`one_per_episode`); the first evaluation of an episode is the bet.
    """
    eps = _episodes(rows)
    n = len(eps)
    scores = [e["score"] for e in eps]
    total = sum(scores, Decimal("0"))
    std = Decimal(str(statistics.stdev(float(x) for x in scores))) if n > 1 else Decimal("0")
    return EpisodeStats(
        n=n,
        n_raw=len(rows),
        n_wins=sum(1 for e in eps if e["pnl_pct"] > 0),
        total_score=total,
        std=std,
        fitness=compute_fitness(n=n, mean=total / n if n else Decimal("0"), std=std),
    )


async def _scored_rows(session, experiment_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[dict]]:
    rows = (await session.execute(
        select(
            LabEvaluation.experiment_id, LabEvaluation.asset_class, LabEvaluation.symbol,
            LabEvaluation.side, LabEvaluation.generated_at, LabEvaluation.close_at,
            LabEvaluation.score, LabEvaluation.pnl_pct,
        )
        .where(LabEvaluation.experiment_id.in_(experiment_ids))
        .where(LabEvaluation.status == "scored")
    )).mappings().all()
    out: dict[uuid.UUID, list[dict]] = {eid: [] for eid in experiment_ids}
    for r in rows:
        out[r["experiment_id"]].append(dict(r))
    return out


async def episode_counts(experiment_ids: list[uuid.UUID]) -> dict[uuid.UUID, tuple[EpisodeStats, int]]:
    """(episode stats, n_unscorable) per experiment, for reports."""
    if not experiment_ids:
        return {}
    async with shared_session_scope() as session:
        scored = await _scored_rows(session, experiment_ids)
        stale = dict((await session.execute(
            select(LabEvaluation.experiment_id, func.count())
            .where(LabEvaluation.experiment_id.in_(experiment_ids))
            .where(LabEvaluation.status == "stale")
            .group_by(LabEvaluation.experiment_id)
        )).all())
    return {eid: (episode_stats(scored[eid]), int(stale.get(eid, 0))) for eid in experiment_ids}


# Genomes whose episodes inform the empirical-Bayes prior and the hour-bucket
# baseline: everything of the asset class scored in this window.
POPULATION_WINDOW = timedelta(days=7)


async def population_episodes(
    asset_class: str, *, since: datetime | None = None
) -> dict[uuid.UUID, list[tuple[datetime, float]]]:
    """(generated_at, score) of every episode, per experiment, for each
    experiment of `asset_class` with a scored evaluation since `since`
    (default: POPULATION_WINDOW ago). Whole histories, time-ordered."""
    since = since or datetime.now(UTC) - POPULATION_WINDOW
    async with shared_session_scope() as session:
        recent = (
            select(LabEvaluation.experiment_id)
            .where(LabEvaluation.asset_class == asset_class)
            .where(LabEvaluation.status == "scored")
            .where(LabEvaluation.generated_at >= since)
            .distinct()
        )
        ids = list((await session.execute(recent)).scalars())
        scored = await _scored_rows(session, ids)
    return {
        eid: [(e["generated_at"], float(e["score"])) for e in _episodes(rows)]
        for eid, rows in scored.items()
    }


def hour_bucket(ts: datetime) -> int:
    return int(ts.timestamp()) // 3600


async def refresh_fitness(experiment_ids: list[uuid.UUID]) -> int:
    """Rewrite each experiment's counters and fitness from its scored episodes.

    n_evaluations / n_wins / total_score are episode counts from here on;
    n_signals stays the number of evaluations opened."""
    if not experiment_ids:
        return 0
    async with shared_session_scope() as session:
        scored = await _scored_rows(session, experiment_ids)
        for eid in experiment_ids:
            exp = await session.get(LabExperiment, eid)
            if exp is None:
                continue
            st = episode_stats(scored[eid])
            exp.n_evaluations = st.n
            exp.n_wins = st.n_wins
            exp.total_score = st.total_score
            exp.fitness_score = st.fitness
    return len(experiment_ids)


def compute_fitness(*, n: int, mean: Decimal, std: Decimal) -> Decimal:
    """Variance-penalised fitness: (mean − k·std/√n) × √(min(n,25)/25).

    Pure so the selection rule is unit-testable. `std` is the sample std of
    per-evaluation capped scores in [-1, 1]; with n < 2 no penalty applies.
    """
    if n <= 0:
        return Decimal("0")
    penalty = (FITNESS_K * std / Decimal(n).sqrt()) if n > 1 and std > 0 else Decimal("0")
    sample_factor = Decimal(str(min(n, FITNESS_FULL_N) / FITNESS_FULL_N)).sqrt()
    return ((mean - penalty) * sample_factor).quantize(Decimal("0.000001"))


async def fresh_symbols(symbols: list[str], now: datetime) -> list[str]:
    """The symbols whose traded perp feed ticked within ENTRY_FRESHNESS_S.

    Pinned to the perp venue: the ticker table also holds `binance`
    funding-poller and `bybit-spot`/`binance-spot` rows under the same symbol,
    and a fresh one of those says nothing about the bybit feed the features
    read (agent.features.PERP_VENUE)."""
    async with local_session_scope() as session:
        last_tick = dict((await session.execute(
            select(TickerSnapshot.symbol, func.max(TickerSnapshot.snapshot_ts))
            .where(TickerSnapshot.symbol.in_(symbols))
            .where(TickerSnapshot.exchange == PERP_VENUE)
            .where(TickerSnapshot.snapshot_ts >= now - timedelta(seconds=ENTRY_FRESHNESS_S))
            .group_by(TickerSnapshot.symbol)
        )).all())
    return [s for s in symbols if s in last_tick]


async def emit_signals(symbols: list[str], asset_class: str = "crypto") -> int:
    """For each active experiment × symbol (filtered by asset_class), emit
    at most one fresh evaluation per pair — and none while that pair still
    has an OPEN evaluation.

    Crypto only for now — BIST features (microstructure) aren't extracted
    yet, so calling this with asset_class='bist' is a no-op until the bar
    feature extractor lands. The asset_class filter on the experiments is
    still applied so the partitioned populations stay independent.
    """
    if asset_class != "crypto":
        # Future: when BIST feature extraction exists, dispatch here.
        return 0

    async with shared_session_scope() as session:
        stmt = (
            select(LabExperiment)
            .where(LabExperiment.status == "active")
            .where(LabExperiment.asset_class == asset_class)
            .order_by(LabExperiment.created_at.asc())
        )
        experiments = list((await session.execute(stmt)).scalars())

    if not experiments:
        return 0

    now = datetime.now(UTC)
    symbols = await fresh_symbols(symbols, now)
    if not symbols:
        return 0

    # Pre-extract features once per symbol (cheap one-shot SQL each)
    feature_cache = {sym: await extract_symbol_features(sym) for sym in symbols}

    # Dedup: one OPEN evaluation per (experiment, symbol). Without this the
    # 20s tick stacked up to horizon/20s near-identical overlapping evals per
    # pair, so n_evaluations counted autocorrelated copies, not samples
    # (docs/AUTONOMY_PLAN.md §5 bug #6).
    async with shared_session_scope() as session:
        open_rows = (await session.execute(
            select(LabEvaluation.experiment_id, LabEvaluation.symbol)
            .where(LabEvaluation.status == "open")
            .where(LabEvaluation.asset_class == asset_class)
        )).all()
    open_pairs = {(eid, sym) for eid, sym in open_rows}

    opened = 0
    for exp in experiments:
        try:
            genome = Genome.from_dict(exp.params)
        except Exception as e:
            logger.warning(f"experiment {exp.id}: bad params, skipping: {e}")
            continue

        for sym in symbols:
            if (exp.id, sym) in open_pairs:
                continue
            f = feature_cache[sym]
            if f.last_price is None:
                continue
            d = decide_with_genome(genome, f)
            if d.side == "hold" or d.confidence < Decimal("0.05"):
                continue

            close_at = now + timedelta(seconds=genome.horizon_seconds)
            async with shared_session_scope() as session:
                session.add(
                    LabEvaluation(
                        experiment_id=exp.id,
                        asset_class=asset_class,
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


async def _mark_price_near(
    symbol: str, target_ts: datetime, asset_class: str = "crypto"
) -> Decimal | None:
    """Find a mark price within the asset_class's window of target_ts (closest)."""
    if asset_class == "bist":
        window = timedelta(seconds=MARK_WINDOW_S_BARS)
        lo = target_ts - window
        hi = target_ts + window
        async with local_session_scope() as session:
            stmt = (
                select(MarketBar.close, MarketBar.ts)
                .where(MarketBar.symbol == symbol)
                .where(MarketBar.asset_class == "bist")
                .where(MarketBar.interval == "1m")
                .where(MarketBar.ts >= lo)
                .where(MarketBar.ts <= hi)
            )
            rows = (await session.execute(stmt)).all()
        if not rows:
            return None
        best = min(rows, key=lambda r: abs((r.ts - target_ts).total_seconds()))
        return Decimal(best.close)

    window = timedelta(seconds=MARK_WINDOW_S)
    lo = target_ts - window
    hi = target_ts + window
    async with local_session_scope() as session:
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
    best = min(rows, key=lambda r: abs((r.trade_ts - target_ts).total_seconds()))
    return Decimal(best.price)


async def score_due_evaluations(stale_after_s: int = 600) -> tuple[int, int]:
    """Score evaluations whose close_at has passed. Returns (scored, stale)."""
    now = datetime.now(UTC)
    cutoff = now - timedelta(seconds=stale_after_s)
    async with shared_session_scope() as session:
        stmt = (
            select(LabEvaluation)
            .where(LabEvaluation.status == "open")
            .where(LabEvaluation.close_at <= now)
            .order_by(LabEvaluation.close_at.asc())
        )
        evals = list((await session.execute(stmt)).scalars())

    scored = 0
    stale = 0
    touched: set[uuid.UUID] = set()
    for ev in evals:
        mark = await _mark_price_near(ev.symbol, ev.close_at, ev.asset_class)
        if mark is None:
            if ev.close_at < cutoff:
                async with shared_session_scope() as session:
                    ev_db = await session.get(LabEvaluation, ev.id)
                    if ev_db is not None:
                        ev_db.status = "stale"
                stale += 1
            continue

        entry_adj = apply_slippage(
            ev.entry_price, ev.side, opening=True, asset_class=ev.asset_class, symbol=ev.symbol
        )
        exit_adj = apply_slippage(
            mark, ev.side, opening=False, asset_class=ev.asset_class, symbol=ev.symbol
        )
        if ev.side == "long":
            pnl_pct = (exit_adj - entry_adj) / entry_adj
        else:
            pnl_pct = (entry_adj - exit_adj) / entry_adj
        capped = max(min(pnl_pct, SCORE_CAP_PCT), -SCORE_CAP_PCT)
        score = capped / SCORE_CAP_PCT  # in [-1, 1]

        async with shared_session_scope() as session:
            ev_db = await session.get(LabEvaluation, ev.id)
            if ev_db is None:
                continue
            ev_db.exit_price = mark
            ev_db.pnl_pct = pnl_pct
            ev_db.score = score
            ev_db.status = "scored"

        scored += 1
        touched.add(ev.experiment_id)

    # Fitness is recomputed from the experiment's episodes rather than
    # incremented per row, so a re-emitted bet can never count twice.
    await refresh_fitness(sorted(touched))
    return scored, stale

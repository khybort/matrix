"""Aggregate outcome metrics per strategy over a time window."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from matrix_shared import shared_session_scope
from matrix_shared.edge_study import episode_groups
from matrix_shared.models import Outcome, Prediction
from sqlalchemy import select


@dataclass(slots=True)
class StrategyMetrics:
    strategy_id: str
    version: int
    n_outcomes: int  # episodes (one per bet), not rows
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
    n_raw: int = 0  # outcome rows behind the n_outcomes episodes
    # Realised net USD per episode (its rows summed), in episode order: the
    # sample the mutation gate tests and efficacy compares (same unit).
    episode_pnls: list[float] = field(default_factory=list)
    n_probes: int = 0  # ε-exploration rows left out (see `is_probe`)


def is_probe(context: dict | None) -> bool:
    """An ε-exploration probe (agent `maybe_explore`): a paper-only sample of
    what the policy would NOT have traded. It is evidence for the lessons
    corridor, not for the exploit policy that reflection mutates and efficacy
    judges — on 2026-09-15/16 probes alone pushed matrix_agent us and bist
    over the mutation gate (bist: 19 episodes, 0 of them the policy's)."""
    return bool((context or {}).get("is_exploration"))


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

    One sample is one bet (`edge_study.episode_groups`): n_outcomes counts
    episodes, win_rate is the share of episodes whose summed pnl is positive,
    avg_score is the mean of per-episode mean scores, total_pnl_usd keeps
    every row's dollars. Per row, a strategy re-filling one losing call ten
    times crossed the mutation gate on a single decision. ε-exploration probes
    are left out (`is_probe`).
    """
    since = datetime.now(UTC) - timedelta(hours=window_hours)
    stmt = (
        select(
            Prediction.strategy_id, Prediction.asset_class, Prediction.symbol, Prediction.side,
            Prediction.generated_at, Prediction.horizon_seconds,
            Outcome.score, Outcome.pnl_usd, Outcome.pnl_pct, Outcome.reason,
            Prediction.context,
        )
        .join(Prediction, Prediction.id == Outcome.prediction_id)
        .where(Prediction.strategy_id == strategy_id)
        .where(Prediction.strategy_version == version)
        .where(Outcome.observed_at >= since)
        .where(Outcome.reason != "orphan_flat_close")
    )
    if asset_class is not None:
        stmt = stmt.where(Prediction.asset_class == asset_class)
    async with shared_session_scope() as session:
        rows = [dict(r) for r in (await session.execute(stmt)).mappings().all()]
    kept = [r for r in rows if not is_probe(r.pop("context"))]
    m = summarize(strategy_id, version, kept, asset_class=asset_class)
    m.n_probes = len(rows) - len(kept)
    return m


def _mean(xs: list) -> Decimal:
    vals = [Decimal(x) for x in xs if x is not None]
    return sum(vals, Decimal("0")) / len(vals) if vals else Decimal("0")


def summarize(
    strategy_id: str, version: int, rows: list[dict], *, asset_class: str | None = None
) -> StrategyMetrics:
    """Episode-counted `StrategyMetrics` from outcome rows (pure)."""
    episodes = episode_groups(sorted(rows, key=lambda r: r["generated_at"]))
    n = len(episodes)
    total = sum((Decimal(r["pnl_usd"] or 0) for r in rows), Decimal("0"))
    wins = sum(1 for g in episodes if sum(Decimal(r["pnl_usd"] or 0) for r in g) > 0)
    scores = [_mean([r["score"] for r in g]) for g in episodes]
    avg = sum(scores, Decimal("0")) / n if n else Decimal("0")

    sym: dict[str, dict] = {}
    rsn: dict[str, dict] = {}
    pnls: list[float] = []
    for g, score in zip(episodes, scores):
        pnl = sum(Decimal(r["pnl_usd"] or 0) for r in g)
        pnls.append(float(pnl))
        s = sym.setdefault(g[0]["symbol"], {"n": 0, "n_raw": 0, "scores": [], "pnl": Decimal("0")})
        s["n"] += 1
        s["n_raw"] += len(g)
        s["scores"].append(score)
        s["pnl"] += pnl
        # The bet's exit labels the episode: re-fills of one call share its fate.
        r = rsn.setdefault(str(g[0]["reason"]), {"n": 0, "n_raw": 0, "pcts": [], "pnl": Decimal("0")})
        r["n"] += 1
        r["n_raw"] += len(g)
        r["pcts"].append(_mean([x["pnl_pct"] for x in g]))
        r["pnl"] += pnl
    by_symbol = {
        k: {"n": str(v["n"]), "n_raw": str(v["n_raw"]),
            "avg_score": str(sum(v["scores"], Decimal("0")) / v["n"]),
            "total_pnl_usd": str(v["pnl"])}
        for k, v in sym.items()
    }
    by_reason = {
        k: {"n": str(v["n"]), "n_raw": str(v["n_raw"]),
            "avg_pnl_bps": str((sum(v["pcts"], Decimal("0")) / v["n"] * 10000).quantize(Decimal("0.1"))),
            "total_pnl_usd": str(v["pnl"])}
        for k, v in rsn.items()
    }
    return StrategyMetrics(
        strategy_id=strategy_id,
        version=version,
        n_outcomes=n,
        avg_score=avg,
        win_rate=Decimal(wins) / Decimal(n) if n else Decimal("0"),
        total_pnl_usd=total,
        by_symbol=by_symbol,
        asset_class=asset_class,
        by_reason=by_reason,
        n_raw=len(rows),
        episode_pnls=pnls,
    )

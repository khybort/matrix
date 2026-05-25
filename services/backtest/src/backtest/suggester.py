"""AI Strategy Suggester — Phase 4.5.

Sweeps a parameter grid against `historical.run_backtest`, scores each
result against a risk-profile-weighted utility, classifies the top
candidates (high_yield / stable / high_frequency), and optionally
attaches an LLM-generated rationale per candidate.

This is Matrix's answer to Bybit's "AI Strategy" suggester: "give me
the best Grid config for BTC over the last 7 days, conservative
risk profile" → ranked JSON list with metrics + rationale.

Usage:
    uv run python -m backtest.suggester \
        --strategy grid --symbol BTCUSDT --days 7 \
        --risk conservative --top-k 5

    # With LLM rationale (requires AI_GATEWAY_API_KEY or matrix-shared LLM env):
    uv run python -m backtest.suggester --strategy grid --symbol BTCUSDT \
        --risk balanced --with-rationale

Conventions:
- Pure functions where possible: scoring, classification, sampling are
  all sync + deterministic (modulo seeded RNG). The async part is the
  backtest sweep and the optional LLM call.
- No defensive validation at internal boundaries — caller's responsibility.
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import os
import random
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from loguru import logger

from backtest.historical import BacktestResult, run_backtest

# ----------------------------------------------------------------- param grids

# Per-strategy default param grids. Override via the `grids` arg of suggest()
# or via CLI flags. Each key maps to the candidate values for that param.
GRID_PARAM_GRID: dict[str, list[Any]] = {
    "n_grids": [5, 8, 10, 15, 20],
    "price_band_pct": [Decimal("0.005"), Decimal("0.01"), Decimal("0.02"),
                       Decimal("0.03"), Decimal("0.05")],
    "horizon_s": [120, 300, 600],
}

# DCA is currently bar-replayed only via grid (one strategy in the historical
# engine). Wiring more strategies here is a follow-up — for now, the suggester
# advertises only the strategies historical.run_backtest knows.
STRATEGY_PARAM_GRIDS: dict[str, dict[str, list[Any]]] = {
    "grid": GRID_PARAM_GRID,
}

# ----------------------------------------------------------------- scoring

# Risk-profile weights for the linear utility score. Calibrated so a small
# safe trade (e.g. $5 pnl, 70% win rate, 2% dd) ranks ABOVE a high-risk big
# winner ($100 pnl, 50% win rate, 20% dd) under the conservative profile,
# and the ranking flips under aggressive. Linear is a Phase 4.5 first-cut —
# Sharpe/Calmar comes when we have variance over time, not aggregates.
RISK_WEIGHTS: dict[str, dict[str, Decimal]] = {
    # Conservative: drawdown is a near-veto. Reward win rate so consistent
    # small wins beat occasional large wins.
    "conservative": {
        "total_pnl_usd": Decimal("1.0"),
        "win_rate": Decimal("50.0"),
        "max_drawdown_pct": Decimal("-500.0"),
        "n_positions": Decimal("0.0"),
    },
    # Balanced: middle ground.
    "balanced": {
        "total_pnl_usd": Decimal("2.0"),
        "win_rate": Decimal("10.0"),
        "max_drawdown_pct": Decimal("-50.0"),
        "n_positions": Decimal("0.0"),
    },
    # Aggressive: raw pnl dominates; drawdown lightly penalized.
    "aggressive": {
        "total_pnl_usd": Decimal("3.0"),
        "win_rate": Decimal("1.0"),
        "max_drawdown_pct": Decimal("-2.0"),
        "n_positions": Decimal("0.0"),
    },
}


@dataclass(slots=True)
class Candidate:
    """One scored param set. Mirrors Bybit's labeled categorization."""

    params: dict[str, Any]
    result: BacktestResult
    score: Decimal
    classification: str  # "high_yield" | "stable" | "high_frequency" | "underperforming"
    rationale: str | None = None  # filled in by LLM when --with-rationale


def score(result: BacktestResult, weights: dict[str, Decimal]) -> Decimal:
    """Linear scoring with normalized inputs.

    Each metric is multiplied by its weight. Drawdown weight is expected
    negative (the per-profile dict already encodes the sign). Doesn't try
    to be a Sharpe ratio — we don't have variance over time, just a single
    run's aggregates.
    """
    return (
        weights["total_pnl_usd"] * Decimal(result.total_pnl_usd)
        + weights["win_rate"] * Decimal(result.win_rate)
        + weights["max_drawdown_pct"] * Decimal(result.max_drawdown_pct)
        + weights["n_positions"] * Decimal(result.n_positions_closed)
    )


def classify(result: BacktestResult, *, all_results: list[BacktestResult]) -> str:
    """Bybit-style label. Relative to the candidate pool — a "high_yield" in
    a calm market may be a "stable" in a wild one."""
    if not all_results:
        return "underperforming"
    pnls = sorted(Decimal(r.total_pnl_usd) for r in all_results)
    dds = sorted(Decimal(r.max_drawdown_pct) for r in all_results)
    freqs = sorted(r.n_positions_closed for r in all_results)
    # Percentile of this result on each axis.
    p_pnl = pnls.index(Decimal(result.total_pnl_usd)) / max(len(pnls) - 1, 1)
    p_dd = dds.index(Decimal(result.max_drawdown_pct)) / max(len(dds) - 1, 1)
    p_freq = freqs.index(result.n_positions_closed) / max(len(freqs) - 1, 1)
    if Decimal(result.total_pnl_usd) <= 0:
        return "underperforming"
    if p_pnl >= 0.66:
        return "high_yield"
    if p_dd <= 0.33:
        return "stable"
    if p_freq >= 0.66:
        return "high_frequency"
    return "stable"


# ----------------------------------------------------------------- sampling


def enumerate_params(
    param_grid: dict[str, list[Any]],
    *,
    max_combinations: int | None = None,
    seed: int | None = None,
) -> list[dict[str, Any]]:
    """Cartesian product of the param grid. If max_combinations < total,
    randomly sample without replacement (seeded if `seed` provided)."""
    keys = list(param_grid.keys())
    full = list(itertools.product(*[param_grid[k] for k in keys]))
    combos = [dict(zip(keys, tup, strict=True)) for tup in full]
    if max_combinations is None or max_combinations >= len(combos):
        return combos
    rng = random.Random(seed) if seed is not None else random.Random()
    return rng.sample(combos, max_combinations)


# ----------------------------------------------------------------- LLM rationale


async def _maybe_llm_rationale(
    candidate: Candidate,
    strategy: str,
    symbol: str,
    risk_profile: str,
) -> str | None:
    """Generate a one-sentence rationale via Claude Haiku. Returns None if
    LLM not configured (matrix_shared.llm_enabled is false) — caller treats
    None as "no rationale," not an error."""
    try:
        from matrix_shared import call_claude_json, llm_enabled
    except ImportError:
        return None
    if not llm_enabled():
        return None

    m = candidate.result
    user_prompt = (
        f"Strategy: {strategy} on {symbol}, risk={risk_profile}.\n"
        f"Params: {json.dumps(candidate.params, default=str)}\n"
        f"Backtest result over {m.n_bars} 1m bars:\n"
        f"  total_pnl_usd={m.total_pnl_usd}\n"
        f"  win_rate={m.win_rate}\n"
        f"  max_drawdown_pct={m.max_drawdown_pct}\n"
        f"  n_positions={m.n_positions_closed}\n"
        f"Classification: {candidate.classification}.\n\n"
        "Write a ONE-sentence rationale (≤ 30 words) for an operator deciding "
        "whether to deploy this config. Output JSON: "
        '{"rationale": "..."}'
    )
    try:
        parsed = await call_claude_json(
            system="You are a quant explaining backtest results to a trader.",
            user=user_prompt,
            model="claude-haiku-4-5",
            max_tokens=120,
            temperature=0.2,
        )
    except Exception as e:
        logger.warning(f"LLM rationale failed: {e}")
        return None
    if not parsed:
        return None
    rationale = parsed.get("rationale")
    if not isinstance(rationale, str):
        return None
    return rationale.strip()[:300]


# ----------------------------------------------------------------- orchestrator


@dataclass(slots=True)
class SuggesterRun:
    strategy: str
    symbol: str
    asset_class: str
    days: int
    risk_profile: str
    n_samples: int
    top_k: int
    candidates: list[Candidate] = field(default_factory=list)


async def suggest(
    *,
    strategy: str,
    symbol: str,
    asset_class: str = "crypto",
    days: int = 7,
    risk_profile: str = "balanced",
    n_samples: int | None = None,
    top_k: int = 5,
    seed: int | None = None,
    with_rationale: bool = False,
    grids: dict[str, list[Any]] | None = None,
) -> SuggesterRun:
    """Param-space sweep → score → top-K + optional LLM rationale."""
    weights = RISK_WEIGHTS[risk_profile]
    param_grid = grids or STRATEGY_PARAM_GRIDS[strategy]
    combos = enumerate_params(param_grid, max_combinations=n_samples, seed=seed)
    logger.info(f"suggester: {len(combos)} param combinations to evaluate")

    results: list[tuple[dict[str, Any], BacktestResult]] = []
    for params in combos:
        try:
            r = await run_backtest(strategy, symbol, asset_class, days, params)
        except Exception as e:
            logger.warning(f"backtest failed for params={params}: {e}")
            continue
        results.append((params, r))

    # Score AFTER all results in, so classify() has the pool to percentile-rank.
    all_results = [r for _, r in results]
    candidates: list[Candidate] = []
    for params, r in results:
        s = score(r, weights)
        c = classify(r, all_results=all_results)
        candidates.append(Candidate(params=params, result=r, score=s, classification=c))

    candidates.sort(key=lambda c: c.score, reverse=True)
    top = candidates[:top_k]

    if with_rationale:
        # Sequential — keeps quota usage bounded; top_k is small (≤ 10).
        for c in top:
            c.rationale = await _maybe_llm_rationale(c, strategy, symbol, risk_profile)

    return SuggesterRun(
        strategy=strategy,
        symbol=symbol,
        asset_class=asset_class,
        days=days,
        risk_profile=risk_profile,
        n_samples=len(combos),
        top_k=top_k,
        candidates=top,
    )


# ----------------------------------------------------------------- CLI


def _serialize_candidate(c: Candidate, *, positions: bool = False) -> dict[str, Any]:
    payload = asdict(c)
    if not positions:
        payload["result"]["positions"] = (
            f"<{len(c.result.positions)} positions, --positions to inline>"
        )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Matrix AI Strategy Suggester")
    parser.add_argument("--strategy", default="grid", choices=list(STRATEGY_PARAM_GRIDS))
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--asset-class", default="crypto")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--risk", default="balanced",
                        choices=list(RISK_WEIGHTS), dest="risk_profile")
    parser.add_argument("--samples", type=int, default=None, dest="n_samples",
                        help="Random-sample N combos instead of full grid")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--with-rationale", action="store_true",
                        help="Ask Claude Haiku for a one-line rationale per candidate")
    parser.add_argument("--positions", action="store_true",
                        help="Include full positions in each candidate's JSON")
    args = parser.parse_args()

    logger.remove()
    logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level: <5} | {message}")
    logger.info(
        f"suggester start: strategy={args.strategy} symbol={args.symbol} "
        f"days={args.days} risk={args.risk_profile} "
        f"samples={args.n_samples or 'full-grid'} top_k={args.top_k}"
    )

    run_ = asyncio.run(
        suggest(
            strategy=args.strategy,
            symbol=args.symbol,
            asset_class=args.asset_class,
            days=args.days,
            risk_profile=args.risk_profile,
            n_samples=args.n_samples,
            top_k=args.top_k,
            seed=args.seed,
            with_rationale=args.with_rationale,
        )
    )

    out = {
        "strategy": run_.strategy,
        "symbol": run_.symbol,
        "asset_class": run_.asset_class,
        "days": run_.days,
        "risk_profile": run_.risk_profile,
        "n_evaluated": run_.n_samples,
        "top_k": run_.top_k,
        "candidates": [_serialize_candidate(c, positions=args.positions) for c in run_.candidates],
        "generated_at": datetime.now().astimezone().isoformat(),
    }
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()

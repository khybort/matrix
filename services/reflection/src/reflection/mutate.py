"""Mutation proposal generator.

Rule-based by default. When AI_GATEWAY_API_KEY is set, LLM proposes
more nuanced changes (with rationale grounded in per-symbol metrics).

CRITICAL: never proposes changes to risk parameters. The Wallet table's
risk fields (max_position_pct, daily_loss_circuit_pct, max_concurrent_positions)
are off-limits to this module. See docs/TRADING.md.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import orjson
from matrix_shared import call_claude_json
from matrix_shared.subscription_llm import MODEL_SONNET

from reflection.metrics import StrategyMetrics

# Single source of truth for MutationDraft + risk-cap stripping.
from reflection.parsing import MutationDraft

# Param-tune table for deterministic strategies and matrix_agent runtime knobs.
# Keys: strategy_id → { param_name → (step, min, max) }.
PARAM_TUNERS: dict[str, dict[str, tuple[Decimal, Decimal, Decimal]]] = {
    "matrix_agent": {
        # horizon_seconds: extend toward the 1800s+ regime where slippage < signal.
        "horizon_seconds": (Decimal("300"), Decimal("600"), Decimal("3600")),
        "signal_threshold": (Decimal("0.02"), Decimal("0.08"), Decimal("0.30")),
        "tp_pct": (Decimal("0.002"), Decimal("0.010"), Decimal("0.040")),
        "sl_pct": (Decimal("0.002"), Decimal("0.005"), Decimal("0.020")),
        "explore_epsilon": (Decimal("0.02"), Decimal("0"), Decimal("0.15")),
    },
    "grid": {
        # price_band_pct: widen/narrow ±price band around the 24h median.
        "price_band_pct": (Decimal("0.005"), Decimal("0.005"), Decimal("0.10")),
        # horizon_s: prediction close window; stored as int in params.
        "horizon_s": (Decimal("60"), Decimal("60"), Decimal("1800")),
        # n_grids: tweak handled via int arithmetic (±2) separately.
        # tp/sl: take-profit and stop-loss percentages.
        "tp_pct": (Decimal("0.002"), Decimal("0.003"), Decimal("0.030")),
        "sl_pct": (Decimal("0.002"), Decimal("0.002"), Decimal("0.015")),
    },
    "dca": {
        # interval_minutes: accumulation cadence; stored as int in params.
        "interval_minutes": (Decimal("15"), Decimal("15"), Decimal("240")),
    },
    "oi_delta": {
        # oi_threshold_pct: maps to OI_JUMP_THRESHOLD in the module.
        "oi_threshold_pct": (Decimal("0.005"), Decimal("0.005"), Decimal("0.10")),
        # horizon_s: prediction close window; stored as int in params.
        "horizon_s": (Decimal("60"), Decimal("60"), Decimal("1800")),
        # tp/sl: take-profit and stop-loss percentages.
        "tp_pct": (Decimal("0.002"), Decimal("0.003"), Decimal("0.030")),
        "sl_pct": (Decimal("0.002"), Decimal("0.002"), Decimal("0.015")),
    },
    "oi_breakout": {
        "oi_threshold_pct": (Decimal("0.005"), Decimal("0.005"), Decimal("0.10")),
        "horizon_s": (Decimal("300"), Decimal("600"), Decimal("3600")),
        "tp_pct": (Decimal("0.002"), Decimal("0.010"), Decimal("0.040")),
        "sl_pct": (Decimal("0.002"), Decimal("0.005"), Decimal("0.020")),
    },
    "funding_reversion": {
        # high_funding: minimum |funding_rate| to trigger a signal (per 8h).
        # Default 0.0002 (0.02%); widen to reduce false fires.
        "high_funding": (Decimal("0.00005"), Decimal("0.00005"), Decimal("0.0010")),
        # funding_cap: |funding_rate| that maps to confidence 1.0.
        # Must stay >= high_funding; step small to preserve proportional scaling.
        "funding_cap": (Decimal("0.00010"), Decimal("0.00010"), Decimal("0.0020")),
        # horizon_s: prediction outcome window in seconds.
        "horizon_s": (Decimal("60"), Decimal("60"), Decimal("1800")),
        # tp/sl: take-profit and stop-loss percentages.
        "tp_pct": (Decimal("0.002"), Decimal("0.003"), Decimal("0.030")),
        "sl_pct": (Decimal("0.002"), Decimal("0.002"), Decimal("0.015")),
    },
    "bist_gap_fade": {
        # gap_threshold: minimum gap % to trigger a signal (1.5% default).
        # Tighten to fire on smaller gaps; widen to reduce noise.
        "gap_threshold": (Decimal("0.005"), Decimal("0.005"), Decimal("0.05")),
        # gap_cap: gap % that maps to confidence 1.0 (5% default).
        "gap_cap": (Decimal("0.005"), Decimal("0.02"), Decimal("0.15")),
        # horizon_s: outcome window; also drives dedup window.
        "horizon_s": (Decimal("60"), Decimal("300"), Decimal("3600")),
        # tp/sl: take-profit and stop-loss percentages.
        "tp_pct": (Decimal("0.002"), Decimal("0.005"), Decimal("0.050")),
        "sl_pct": (Decimal("0.002"), Decimal("0.003"), Decimal("0.025")),
    },
    "bist_intraday_reversion": {
        # drop_threshold: minimum intraday drop from session-open to trigger (3% default).
        "drop_threshold": (Decimal("0.005"), Decimal("0.010"), Decimal("0.08")),
        # drop_cap: drop % that maps to confidence 1.0 (7% default).
        "drop_cap": (Decimal("0.005"), Decimal("0.03"), Decimal("0.15")),
        # horizon_s: hold window for the reversion play (3600s default).
        "horizon_s": (Decimal("60"), Decimal("600"), Decimal("7200")),
        # tp/sl: take-profit and stop-loss percentages.
        "tp_pct": (Decimal("0.002"), Decimal("0.005"), Decimal("0.050")),
        "sl_pct": (Decimal("0.002"), Decimal("0.005"), Decimal("0.030")),
    },
    "bist_volume_breakout": {
        # vol_mult: current bar must exceed this multiple of rolling avg volume (3× default).
        # Raise to require stronger signal; lower to fire more often.
        "vol_mult": (Decimal("0.5"), Decimal("1.5"), Decimal("10.0")),
        # vol_mult_cap: volume ratio that maps to confidence 1.0 (8× default).
        "vol_mult_cap": (Decimal("0.5"), Decimal("3.0"), Decimal("20.0")),
        # horizon_s: momentum hold window (900s default).
        "horizon_s": (Decimal("60"), Decimal("300"), Decimal("1800")),
        # tp/sl: take-profit and stop-loss percentages.
        "tp_pct": (Decimal("0.003"), Decimal("0.010"), Decimal("0.060")),
        "sl_pct": (Decimal("0.002"), Decimal("0.005"), Decimal("0.030")),
    },
    "momentum_xs": {
        # lookback_days: window for computing cross-sectional return ranking.
        "lookback_days": (Decimal("1"), Decimal("3"), Decimal("30")),
        # top_k: number of symbols to long/short at each tick.
        "top_k": (Decimal("1"), Decimal("1"), Decimal("5")),
        # vol_filter_pct: max hourly-return std before excluding a symbol.
        "vol_filter_pct": (Decimal("0.10"), Decimal("0.20"), Decimal("1.00")),
        # horizon_s: prediction close window.
        "horizon_s": (Decimal("300"), Decimal("300"), Decimal("7200")),
        # tp/sl: take-profit and stop-loss percentages.
        "tp_pct": (Decimal("0.005"), Decimal("0.010"), Decimal("0.060")),
        "sl_pct": (Decimal("0.003"), Decimal("0.005"), Decimal("0.030")),
    },
    "screener_follow": {
        # min_passes: consecutive qualifying polls required before acting.
        "min_passes":  (Decimal("1"), Decimal("1"), Decimal("10")),
        # min_score: minimum screener score (|funding_rate| or oi_delta_pct).
        "min_score":   (Decimal("0.01"), Decimal("0.01"), Decimal("0.50")),
        # horizon_s: prediction close window and cooldown gate.
        "horizon_s":   (Decimal("300"), Decimal("300"), Decimal("7200")),
        # tp/sl: take-profit and stop-loss percentages.
        "tp_pct": (Decimal("0.002"), Decimal("0.005"), Decimal("0.040")),
        "sl_pct": (Decimal("0.002"), Decimal("0.003"), Decimal("0.020")),
    },
    "cash_and_carry": {
        # min_funding: floor funding rate (per 8h) to enter a delta-neutral position.
        # Tighten to enter more aggressively; widen to require stronger carry.
        "min_funding": (Decimal("0.00005"), Decimal("0.00005"), Decimal("0.0010")),
        # horizon_s: outcome window in seconds (default 8h = 28800).
        # Wider horizons capture more funding intervals; narrower exits faster
        # if funding flips mid-hold.
        "horizon_s": (Decimal("3600"), Decimal("3600"), Decimal("86400")),
        # NOTE: cash_and_carry is delta-neutral — no tp/sl (funding-flip exit only).
    },
    "bist_news_event": {
        # horizon_s: reaction window; wider gives the news signal more time to play.
        "horizon_s": (Decimal("60"), Decimal("600"), Decimal("7200")),
        # tp/sl: take-profit and stop-loss percentages.
        "tp_pct": (Decimal("0.003"), Decimal("0.010"), Decimal("0.060")),
        "sl_pct": (Decimal("0.002"), Decimal("0.005"), Decimal("0.030")),
    },
}

LLM_MODEL = MODEL_SONNET

# Mutation triggers — fire when PnL is negative OR avg score is poor.
NEG_AVG_SCORE_TRIGGER = Decimal("-0.05")
MIN_N_OUTCOMES = 10

# How aggressive each mutation step is
WEIGHT_PERTURB = Decimal("0.05")  # shift toward alpha features
THRESHOLD_PERTURB = Decimal("0.03")  # loosen threshold when losing (CHANGES.md)
HORIZON_STEP_S = 300
TARGET_HORIZON_S = 1800

# Empirically stronger long-horizon features (2026-05-26 diagnosis).
ALPHA_FEATURES = ("oi_delta", "news")
NOISE_FEATURES = ("trade_flow", "ob_imbalance", "funding")


def _normalize_weights(weights: dict[str, Decimal]) -> dict[str, Decimal]:
    total = sum(weights.values(), Decimal("0"))
    if total <= 0:
        return weights
    return {k: v / total for k, v in weights.items()}


def _underperforming(
    m: StrategyMetrics,
    *,
    min_outcomes: int,
    score_trigger: Decimal,
) -> bool:
    """True when we have enough samples and paper PnL or score says 'fix this'."""
    if m.n_outcomes < min_outcomes:
        return False
    if m.total_pnl_usd < 0:
        return True
    return m.avg_score < score_trigger


def _merge_params(current: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge a mutation patch into the live params dict."""
    out = dict(current or {})
    for key, val in patch.items():
        if key == "weights" and isinstance(val, dict):
            merged_w = dict(out.get("weights") or {})
            merged_w.update(val)
            out["weights"] = merged_w
        else:
            out[key] = val
    return out


def rule_propose(
    current_params: dict[str, Any],
    m: StrategyMetrics,
    *,
    min_outcomes: int = MIN_N_OUTCOMES,
    score_trigger: Decimal = NEG_AVG_SCORE_TRIGGER,
) -> MutationDraft | None:
    """When paper PnL is negative, shift weight toward oi_delta/news and loosen
    threshold / extend horizon — opposite of the pre-2026-06 heuristic that
    dampened news and raised threshold (which contradicted CHANGES.md diagnosis).
    """
    if not _underperforming(m, min_outcomes=min_outcomes, score_trigger=score_trigger):
        return None

    weights = {k: Decimal(str(v)) for k, v in current_params.get("weights", {}).items()}
    if not weights:
        return None
    before = dict(weights)

    # Move mass from noisy microstructure features toward oi_delta + news.
    shift = WEIGHT_PERTURB
    noise_pool = sum(weights.get(k, Decimal("0")) for k in NOISE_FEATURES)
    if noise_pool >= shift:
        taken = Decimal("0")
        for feat in NOISE_FEATURES:
            if taken >= shift:
                break
            w = weights.get(feat, Decimal("0"))
            if w <= 0:
                continue
            cut = min(w, shift - taken)
            weights[feat] = w - cut
            taken += cut
        if taken > 0:
            alpha_share = taken / Decimal(len(ALPHA_FEATURES))
            for feat in ALPHA_FEATURES:
                weights[feat] = weights.get(feat, Decimal("0")) + alpha_share

    weights = _normalize_weights(weights)

    # Loosen threshold — tighter thresholds hurt win rate at short horizons.
    sig_thr = Decimal(str(current_params.get("signal_threshold", "0.18")))
    sig_thr_new = max(sig_thr - THRESHOLD_PERTURB, Decimal("0.08"))

    horizon = int(current_params.get("horizon_seconds", 120))
    horizon_new = horizon
    if horizon < TARGET_HORIZON_S:
        horizon_new = min(horizon + HORIZON_STEP_S, TARGET_HORIZON_S)

    explore = float(current_params.get("explore_epsilon", 0.15))
    explore_new = max(explore - 0.05, 0.02)

    patch = {
        "weights": {k: str(v.quantize(Decimal("0.0001"))) for k, v in weights.items()},
        "signal_threshold": str(sig_thr_new.quantize(Decimal("0.0001"))),
        "horizon_seconds": horizon_new,
        "explore_epsilon": round(explore_new, 3),
    }
    after = _merge_params(current_params, patch)
    before_serializable = _merge_params(
        current_params,
        {
            "weights": {k: str(v) for k, v in before.items()},
            "signal_threshold": str(sig_thr),
            "horizon_seconds": horizon,
            "explore_epsilon": explore,
        },
    )

    rationale = (
        f"total_pnl_usd={m.total_pnl_usd:.2f} avg_score={m.avg_score:.4f} over "
        f"{m.n_outcomes} outcomes (win_rate={m.win_rate:.3f}). Shifting weight "
        f"toward oi_delta/news, lowering signal_threshold, extending horizon toward "
        f"{TARGET_HORIZON_S}s, reducing exploration."
    )

    return MutationDraft(
        proposal_type="weight_tune",
        before_params=before_serializable,
        after_params=after,
        rationale=rationale,
        source="rule",
    )


def rule_propose_param_tune(
    strategy_id: str,
    current_params: dict[str, Any],
    m: StrategyMetrics,
    *,
    min_outcomes: int = MIN_N_OUTCOMES,
    score_trigger: Decimal = NEG_AVG_SCORE_TRIGGER,
) -> MutationDraft | None:
    """For deterministic strategies (grid/dca/oi_delta), perturb numeric params
    when win-rate / avg_score signals underperformance.

    Heuristic per strategy:
      grid:     loss → widen price_band_pct (fewer false fills) or raise horizon_s
      dca:      sustained loss → lengthen interval_minutes (slower accumulator)
      oi_delta: low win rate → raise oi_threshold_pct (only react to bigger moves)

    Direction: when win_rate < 0.5 we assume the signal fires too often →
    widen / lengthen (direction = +1). When win_rate >= 0.5 the signal is
    accurate but still losing money → tighten slightly (direction = -1).

    The knob to tune is selected deterministically by rotating over
    sorted param names using (n_outcomes mod n_knobs) so consecutive
    proposals explore different parts of the search space.
    """
    if strategy_id not in PARAM_TUNERS:
        return None
    if not _underperforming(m, min_outcomes=min_outcomes, score_trigger=score_trigger):
        return None

    tuner = PARAM_TUNERS[strategy_id]
    if not tuner:
        return None

    # Pick one knob deterministically based on outcome count.
    param_names = sorted(tuner.keys())
    idx = m.n_outcomes % len(param_names)
    knob = param_names[idx]
    step, lo, hi = tuner[knob]

    # Read current value — skip if missing (params not seeded yet).
    raw_current = current_params.get(knob)
    if raw_current is None:
        return None
    try:
        current = Decimal(str(raw_current))
    except (ArithmeticError, ValueError):
        return None

    # Per-knob direction: widen/lengthen when PnL negative or win_rate poor.
    # High win_rate + mildly negative score but positive PnL → tighten instead.
    losing = m.total_pnl_usd < 0 or m.win_rate < Decimal("0.5")
    if knob in {"signal_threshold", "explore_epsilon"}:
        direction = Decimal("-1") if losing else Decimal("1")
    else:
        direction = Decimal("1") if losing else Decimal("-1")
    new_value = current + direction * step
    new_value = max(lo, min(hi, new_value))
    if new_value == current:
        return None

    # Build after_params preserving all existing keys.
    after_params = dict(current_params)
    before_params = dict(current_params)

    # Integer params stay integer; decimal params serialized as strings.
    int_params = {"horizon_s", "interval_minutes", "horizon_seconds", "lookback_days", "top_k", "min_passes", "n_grids"}
    if knob in int_params:
        after_params[knob] = int(new_value)
    elif knob == "explore_epsilon":
        after_params[knob] = float(new_value)
    else:
        after_params[knob] = str(new_value.quantize(Decimal("0.0001")))

    rationale = (
        f"{strategy_id}: avg_score={m.avg_score:.4f}, win_rate={m.win_rate:.3f}, "
        f"total_pnl_usd={m.total_pnl_usd:.2f} over {m.n_outcomes} outcomes. "
        f"Adjusting {knob}: {current} → {new_value}."
    )

    return MutationDraft(
        proposal_type="param_tune",
        before_params=before_params,
        after_params=after_params,
        rationale=rationale,
        source="rule",
    )


async def llm_propose(
    strategy_id: str, current_params: dict[str, Any], m: StrategyMetrics
) -> MutationDraft | None:
    """LLM-driven proposal. Falls back to None if LLM unreachable."""
    user_prompt = (
        f"Strategy: {strategy_id}\n"
        f"Current params: {orjson.dumps(current_params).decode()}\n"
        f"Recent metrics (24h):\n"
        f"  n_outcomes={m.n_outcomes}\n"
        f"  avg_score={m.avg_score}\n"
        f"  win_rate={m.win_rate}\n"
        f"  total_pnl_usd={m.total_pnl_usd}\n"
        f"  by_symbol={orjson.dumps(m.by_symbol).decode()}\n\n"
        "Propose ONE parameter mutation that could improve **total_pnl_usd** "
        "(not just average score). Prefer extending horizon_seconds toward "
        "1800+ and shifting weight toward oi_delta/news when losing. "
        "DO NOT propose changes to risk caps (max_position_pct, "
        "daily_loss_circuit_pct, max_concurrent_positions) — those are off-limits. "
        "Respond with JSON: "
        '{"proposal_type":"weight_tune|threshold_change","after_params":{...},'
        '"rationale":"..."} '
        "or {} if no change recommended."
    )
    system = (
        "You are a quantitative research assistant proposing parameter "
        "adjustments for a trading strategy. Be conservative and explicit "
        "in your reasoning. Never touch risk caps."
    )
    parsed = await call_claude_json(
        system=system, user=user_prompt, model=LLM_MODEL,
        max_tokens=400, temperature=0.3,
    )
    # Centralized validation + risk-cap stripping lives in reflection.parsing
    # so the legacy path and the agent path share one chokepoint.
    from reflection.parsing import parse_mutation_draft
    return parse_mutation_draft(parsed, current_params=current_params, source="llm")

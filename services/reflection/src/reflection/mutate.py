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

# ---------------------------------------------------------------------------
# Param-tune table for deterministic strategies (grid / dca / oi_delta).
# Keys: strategy_id → { param_name → (step, min, max) }.
# matrix_agent is intentionally absent — it uses the weight tuner path.
# ---------------------------------------------------------------------------
PARAM_TUNERS: dict[str, dict[str, tuple[Decimal, Decimal, Decimal]]] = {
    "grid": {
        # price_band_pct: widen/narrow ±price band around the 24h median.
        "price_band_pct": (Decimal("0.005"), Decimal("0.005"), Decimal("0.10")),
        # horizon_s: prediction close window; stored as int in params.
        "horizon_s": (Decimal("60"), Decimal("60"), Decimal("1800")),
        # n_grids: tweak handled via int arithmetic (±2) separately.
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
    },
}

LLM_MODEL = MODEL_SONNET

# Mutation triggers
NEG_AVG_SCORE_TRIGGER = Decimal("-0.05")  # below this avg score → propose mutation
MIN_N_OUTCOMES = 10  # need at least this many outcomes to act

# How aggressive each mutation step is
WEIGHT_PERTURB = Decimal("0.1")  # max ±10% relative to current weight
THRESHOLD_PERTURB = Decimal("0.05")  # absolute step


def _normalize_weights(weights: dict[str, Decimal]) -> dict[str, Decimal]:
    total = sum(weights.values(), Decimal("0"))
    if total <= 0:
        return weights
    return {k: v / total for k, v in weights.items()}


def rule_propose(
    current_params: dict[str, Any],
    m: StrategyMetrics,
    *,
    min_outcomes: int = MIN_N_OUTCOMES,
    score_trigger: Decimal = NEG_AVG_SCORE_TRIGGER,
) -> MutationDraft | None:
    """If win rate poor, perturb weights toward signals that performed best.

    Logic: rebalance weights inversely to per-symbol performance is hard
    without per-feature attribution, so we use a simpler heuristic — when
    avg_score is negative, **dampen the news weight** (the noisiest feature)
    and re-distribute to the more deterministic signals.
    """
    if m.n_outcomes < min_outcomes or m.avg_score >= score_trigger:
        return None

    weights = {k: Decimal(v) for k, v in current_params.get("weights", {}).items()}
    if not weights:
        return None
    before = dict(weights)

    # Dampen news weight; redistribute reduction proportionally to others.
    news = weights.get("news", Decimal("0"))
    if news > Decimal("0.05"):
        reduction = news * Decimal("0.5")
        weights["news"] = news - reduction
        others = {k: v for k, v in weights.items() if k != "news"}
        others_sum = sum(others.values(), Decimal("0"))
        if others_sum > 0:
            for k in others:
                weights[k] = weights[k] + reduction * (weights[k] / others_sum)

    weights = _normalize_weights(weights)

    # Also raise signal_threshold slightly to filter weaker signals
    sig_thr = Decimal(current_params.get("signal_threshold", "0.18"))
    sig_thr_new = min(sig_thr + THRESHOLD_PERTURB, Decimal("0.5"))

    after = {
        "weights": {k: str(v.quantize(Decimal("0.0001"))) for k, v in weights.items()},
        "signal_threshold": str(sig_thr_new.quantize(Decimal("0.0001"))),
    }
    before_serializable = {
        "weights": {k: str(v) for k, v in before.items()},
        "signal_threshold": str(sig_thr),
    }

    rationale = (
        f"avg_score={m.avg_score:.4f} over {m.n_outcomes} outcomes is below "
        f"the {NEG_AVG_SCORE_TRIGGER} trigger. Dampening news weight (noisiest "
        f"feature) and tightening signal_threshold to reduce false signals. "
        f"win_rate={m.win_rate:.3f} total_pnl_usd={m.total_pnl_usd:.2f}."
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
    if m.n_outcomes < min_outcomes or m.avg_score >= score_trigger:
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

    # Direction: widen/lengthen when win_rate is poor (signal fires too often).
    direction = Decimal("1") if m.win_rate < Decimal("0.5") else Decimal("-1")
    new_value = current + direction * step
    new_value = max(lo, min(hi, new_value))
    if new_value == current:
        return None

    # Build after_params preserving all existing keys.
    after_params = dict(current_params)
    before_params = dict(current_params)

    # Integer params stay integer; decimal params serialized as strings.
    if knob in {"horizon_s", "interval_minutes"}:
        after_params[knob] = int(new_value)
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
        "Propose ONE parameter mutation that could improve average score. "
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

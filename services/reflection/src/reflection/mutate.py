"""Mutation proposal generator.

Rule-based by default. When AI_GATEWAY_API_KEY is set, LLM proposes
more nuanced changes (with rationale grounded in per-symbol metrics).

CRITICAL: never proposes changes to risk parameters. The Wallet table's
risk fields (max_position_pct, daily_loss_circuit_pct, max_concurrent_positions)
are off-limits to this module. See docs/TRADING.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import orjson
from loguru import logger

from matrix_shared import call_claude_json

from reflection.metrics import StrategyMetrics

LLM_MODEL = "claude-haiku-4-5"

# Mutation triggers
NEG_AVG_SCORE_TRIGGER = Decimal("-0.05")  # below this avg score → propose mutation
MIN_N_OUTCOMES = 10  # need at least this many outcomes to act

# How aggressive each mutation step is
WEIGHT_PERTURB = Decimal("0.1")  # max ±10% relative to current weight
THRESHOLD_PERTURB = Decimal("0.05")  # absolute step


@dataclass(slots=True)
class MutationDraft:
    proposal_type: str
    before_params: dict[str, Any]
    after_params: dict[str, Any]
    rationale: str
    source: str  # "rule" | "llm"


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
    if not parsed:
        return None

    proposal_type = str(parsed.get("proposal_type", "")).strip()
    if proposal_type not in ("weight_tune", "threshold_change", "prompt_change"):
        return None
    after = parsed.get("after_params", {})
    if not isinstance(after, dict) or not after:
        return None
    # Strip any attempted risk-cap fields just in case
    for forbidden in (
        "max_position_pct",
        "daily_loss_circuit_pct",
        "max_concurrent_positions",
        "live_capital_cap_usd",
        "live_execution_enabled",
    ):
        after.pop(forbidden, None)

    rationale = str(parsed.get("rationale", "(no rationale)"))[:2000]
    return MutationDraft(
        proposal_type=proposal_type,
        before_params=current_params,
        after_params=after,
        rationale=rationale,
        source="llm",
    )

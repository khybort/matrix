"""Decision engine: rule-based scoring, with optional LLM override.

Inputs: SymbolFeatures
Outputs: Decision with side, confidence, thesis, full feature context

The rule-based path is deterministic and free to evaluate. The LLM path
adds context-aware reasoning when AI_GATEWAY_API_KEY is configured.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from loguru import logger
from matrix_shared.agent_lessons import lessons_relevant_to

from agent.config import FALLBACK, AgentConfig
from agent.features import SymbolFeatures
from agent.llm import call_llm_decision, llm_enabled

# Lessons with confidence at or above this threshold change behavior.
# 0.4 captures buckets at ~25+ observations on the synthesizer's confidence
# curve — enough signal to act on, low enough to catch fast-bleeding patterns
# before the bucket grows. Tighten later if false-positives appear.
LESSON_CONFIDENCE_GATE = Decimal("0.4")

# Defaults kept only for stand-alone / test invocations; the live loop loads
# the current AgentConfig from DB and passes it explicitly.
WEIGHTS: dict[str, Decimal] = dict(FALLBACK.weights)
RULE_SIGNAL_THRESHOLD = FALLBACK.signal_threshold

# Epsilon-greedy exploration: probability of taking a paper trade the
# signal_threshold would otherwise skip, to feed the learning loop more
# samples (esp. in regions the exploit policy avoids). Tagged is_exploration
# so outcomes are identifiable. Paper-only — never relaxes the live gate.
EXPLORE_EPSILON_DEFAULT = float(FALLBACK.explore_epsilon)
EXPLORE_CONFIDENCE = Decimal("0.05")


@dataclass(slots=True)
class Decision:
    symbol: str
    side: str  # long | short | hold
    confidence: Decimal
    thesis: str
    method: str  # "rule" | "llm"
    feature_dump: dict[str, Any] = field(default_factory=dict)
    last_price: Decimal | None = None


def _trade_flow_score(f: SymbolFeatures) -> Decimal:
    """Mean-reversion. Strong buy_share → expect snap-back → short (negative)."""
    if f.n_trades_60s < 5:
        return Decimal("0")
    # buy_share ∈ [0,1]; map to [-1,1] then invert (extreme buy → short)
    raw = (f.buy_share_60s - Decimal("0.5")) * Decimal("2")
    return -raw  # invert for reversion


def _funding_score(f: SymbolFeatures) -> Decimal:
    """High positive funding (longs paying) → reversion long fade; short bias."""
    if f.funding_rate is None:
        return Decimal("0")
    # Funding rates typically range from -0.5% to +0.5% per 8h interval.
    # Map ±0.05% → ±1; clamp.
    fr = f.funding_rate
    scaled = fr / Decimal("0.0005")
    scaled = max(min(scaled, Decimal("1")), Decimal("-1"))
    return -scaled  # high funding → bias short (reversion)


def _oi_score(f: SymbolFeatures) -> Decimal:
    """OI jump + price up → momentum continuation. We don't yet track price
    delta separately; use OI direction as a proxy momentum nudge."""
    if f.oi_delta_pct_5m is None:
        return Decimal("0")
    # ±2% OI swing → ±1
    scaled = f.oi_delta_pct_5m / Decimal("0.02")
    return max(min(scaled, Decimal("1")), Decimal("-1"))


def _ob_imbalance_score(f: SymbolFeatures) -> Decimal:
    """Bid-stack heavy → bullish microstructure. Map [0,1] → [-1,1]."""
    if f.bid_ask_imbalance_top5 is None:
        return Decimal("0")
    return (f.bid_ask_imbalance_top5 - Decimal("0.5")) * Decimal("2")


def _news_score(f: SymbolFeatures) -> Decimal:
    """Graph-driven news sentiment.

    Blend of three graph-derived signals:
      direct  — polarity of titles directly mentioning the asset
      context — polarity of titles about companies 2-hop connected to it
      recency — exponential-decay-weighted mention count, capped

    When the graph has no coverage for this asset (early bootstrap),
    falls back to naive title-keyword sentiment so the agent isn't blind.
    """
    has_graph = (
        f.graph_mention_count > 0
        or len(f.graph_related_companies) > 0
        or len(f.graph_co_mentioned_assets) > 0
    )
    if has_graph:
        direct = f.graph_direct_polarity
        context = f.graph_contextual_polarity
        blended = (direct * Decimal("0.7")) + (context * Decimal("0.3"))
        # ramp 0..1 as recency-weighted count climbs to ~3
        scale = min(Decimal("1"), f.graph_recency_weight / Decimal("3"))
        return blended * scale

    # Fallback when graph empty / asset uncovered
    if not f.news_titles_sample:
        return Decimal("0")
    bullish_kw = (
        "surge", "rally", "soar", "breakout", "approve", "etf", "adoption",
        "rises", "bullish", "high",
    )
    bearish_kw = (
        "crash", "plunge", "drop", "ban", "hack", "exploit", "lawsuit",
        "investigation", "bearish", "selloff", "liquidat",
    )
    pos = 0
    neg = 0
    for t in f.news_titles_sample:
        tl = t.lower()
        pos += sum(1 for k in bullish_kw if k in tl)
        neg += sum(1 for k in bearish_kw if k in tl)
    if pos + neg == 0:
        return Decimal("0")
    return Decimal(pos - neg) / Decimal(pos + neg)


_CRYPTO_ONLY_SIGNALS = ("funding", "oi_delta")


def _asset_weights(
    weights: dict[str, Decimal], asset_class: str
) -> dict[str, Decimal]:
    """Zero out crypto-only signals for non-crypto asset classes and renormalize.

    Renormalization preserves the relative emphasis the lab/reflection placed
    on the remaining signals. If the surviving total is zero (degenerate
    config), we leave the weights alone.
    """
    if asset_class == "crypto":
        return weights
    surviving = {k: v for k, v in weights.items() if k not in _CRYPTO_ONLY_SIGNALS}
    total = sum(surviving.values(), Decimal("0"))
    if total <= 0:
        return surviving
    scale = sum(weights.values(), Decimal("0")) / total
    return {k: v * scale for k, v in surviving.items()}


def rule_decide(
    f: SymbolFeatures,
    weights: dict[str, Decimal] = WEIGHTS,
    signal_threshold: Decimal = RULE_SIGNAL_THRESHOLD,
    asset_class: str = "crypto",
) -> Decision:
    """Linear-combine signals, threshold to decide side.

    `asset_class` controls which signals contribute: crypto-only features
    (funding, oi_delta) are dropped for non-crypto classes and the remaining
    weights are renormalized to keep the threshold scale comparable.
    """
    weights = _asset_weights(weights, asset_class)
    sub = {
        "trade_flow": _trade_flow_score(f),
        "funding": _funding_score(f),
        "oi_delta": _oi_score(f),
        "ob_imbalance": _ob_imbalance_score(f),
        "news": _news_score(f),
    }
    # Drop crypto-only contributions when not applicable.
    for k in _CRYPTO_ONLY_SIGNALS:
        if k not in weights:
            sub[k] = Decimal("0")

    total = sum((weights.get(k, Decimal("0")) * v for k, v in sub.items()), Decimal("0"))

    if total >= signal_threshold:
        side = "long"
        conf = min(Decimal("1"), total)
    elif total <= -signal_threshold:
        # BIST is long-only; suppress short emissions at the agent layer.
        if asset_class == "bist":
            side = "hold"
            conf = Decimal("0")
        else:
            side = "short"
            conf = min(Decimal("1"), abs(total))
    else:
        side = "hold"
        conf = Decimal("0")

    thesis = (
        f"score={total:.3f} | "
        + " | ".join(f"{k}={v:.2f}" for k, v in sub.items())
    )

    dump = {
        "scores": {k: str(v) for k, v in sub.items()},
        "total": str(total),
        "weights": {k: str(v) for k, v in weights.items()},
        "features": _feature_dump(f),
    }

    return Decision(
        symbol=f.symbol,
        side=side,
        confidence=conf,
        thesis=thesis,
        method="rule",
        feature_dump=dump,
        last_price=f.last_price,
    )


def _feature_dump(f: SymbolFeatures) -> dict[str, Any]:
    return {
        "last_price": str(f.last_price) if f.last_price else None,
        "n_trades_60s": f.n_trades_60s,
        "buy_share_60s": str(f.buy_share_60s),
        "notional_60s_usd": str(f.notional_60s_usd),
        "spread_bps": str(f.spread_bps) if f.spread_bps else None,
        "ob_imbalance_top5": str(f.bid_ask_imbalance_top5)
        if f.bid_ask_imbalance_top5
        else None,
        "funding_rate": str(f.funding_rate) if f.funding_rate else None,
        "open_interest": str(f.open_interest) if f.open_interest else None,
        "oi_delta_pct_5m": str(f.oi_delta_pct_5m) if f.oi_delta_pct_5m else None,
        "n_news_1h": f.n_news_1h,
        "news_titles_sample": f.news_titles_sample,
        "graph": {
            "mention_count": f.graph_mention_count,
            "recency_weight": str(f.graph_recency_weight),
            "direct_polarity": str(f.graph_direct_polarity),
            "contextual_polarity": str(f.graph_contextual_polarity),
            "related_companies": f.graph_related_companies,
            "co_mentioned_assets": f.graph_co_mentioned_assets,
        },
    }


def _llm_prompt(f: SymbolFeatures) -> str:
    parts = [
        f"Symbol: {f.symbol}",
        f"Last price: {f.last_price}",
        f"Trade flow 60s: n={f.n_trades_60s} buy_share={f.buy_share_60s:.3f} "
        f"notional_usd={f.notional_60s_usd}",
        f"Orderbook: spread_bps={f.spread_bps} top5_bid_share={f.bid_ask_imbalance_top5}",
        f"Funding rate: {f.funding_rate}",
        f"OI={f.open_interest} ΔOI_5m={f.oi_delta_pct_5m}",
        f"News last 1h: n={f.n_news_1h}",
    ]
    if f.news_titles_sample:
        parts.append("Recent titles:")
        for t in f.news_titles_sample:
            parts.append(f"- {t}")
    return "\n".join(parts)


def _exploration_scale(edge: float | None) -> float:
    """Scale factor [0, 1] on base epsilon from per-symbol realized edge.

    Edge in [0, 1]; 0.5 = neutral (no data or shrunk average).
    - Unknown/neutral (0.4–0.6): full exploration (scale=1.0) — gather samples.
    - Established positive (> 0.6): reduced exploration (0.5) — already exploiting.
    - Established negative (< 0.4): zero exploration — stop burning slots on a
      symbol the system has evidence is unprofitable.
    """
    if edge is None:
        return 1.0
    if edge < 0.4:
        return 0.0
    if edge > 0.6:
        return 0.5
    return 1.0


def maybe_explore(
    d: Decision,
    *,
    epsilon: float,
    roll: float,
    asset_class: str = "crypto",
    explore_conf: Decimal = EXPLORE_CONFIDENCE,
    symbol_edge: float | None = None,
) -> Decision:
    """With probability epsilon, flip a `hold` into a low-confidence trade.

    epsilon is scaled by symbol_edge (see _exploration_scale): symbols with
    established negative edge are never explored; unknowns are always explored;
    high-edge symbols are explored at reduced rate.

    Direction follows the sub-threshold signal lean (feature_dump['total']).
    BIST is long-only, so a negative lean stays hold. Non-hold decisions are
    returned untouched. The result is tagged is_exploration and still passes
    through the lessons gate downstream (so `avoid` can veto it).
    """
    eff_epsilon = epsilon * _exploration_scale(symbol_edge)
    if d.side != "hold" or roll >= eff_epsilon:
        return d
    try:
        total = Decimal(str(d.feature_dump.get("total", "0")))
    except (ArithmeticError, ValueError):
        total = Decimal("0")
    if total > 0:
        side = "long"
    elif total < 0:
        side = "short"
    else:
        side = "long"  # no lean → pick a direction to gather a sample
    if asset_class == "bist" and side == "short":
        return d  # long-only market: don't explore shorts
    return Decision(
        symbol=d.symbol,
        side=side,
        confidence=explore_conf,
        thesis=f"EXPLORE (ε) lean={total:.3f} | {d.thesis}"[:1000],
        method=f"{d.method}+explore",
        feature_dump={**d.feature_dump, "is_exploration": True},
        last_price=d.last_price,
    )


async def decide(
    f: SymbolFeatures,
    cfg: AgentConfig | None = None,
    asset_class: str = "crypto",
    strategy_id: str = "matrix_agent",
    explore_rand: Callable[[], float] = random.random,
    symbol_edge: float | None = None,
) -> Decision:
    """Top-level decision: rule-based by default; LLM if enabled.

    The LLM result, when present, overrides the rule decision but the rule
    decision is still computed and stored in feature_dump for audit.

    Active `avoid` lessons from agent_lessons gate the final side: if the
    proposed (symbol, side) matches an avoid verdict with confidence >=
    LESSON_CONFIDENCE_GATE, side is forced to hold. `prefer` verdicts
    don't flip the side but bump confidence by a fixed clamp.
    """
    if cfg is not None:
        rule = rule_decide(f, cfg.weights, cfg.signal_threshold, asset_class=asset_class)
    else:
        rule = rule_decide(f, asset_class=asset_class)

    if llm_enabled():
        llm = await call_llm_decision(_llm_prompt(f))
        if llm is not None:
            base = Decision(
                symbol=f.symbol,
                side=llm.side,
                confidence=Decimal(str(llm.confidence)),
                thesis=f"LLM: {llm.reasoning} || rule: {rule.thesis}",
                method="llm",
                feature_dump={**rule.feature_dump, "llm_confidence": llm.confidence},
                last_price=f.last_price,
            )
        else:
            logger.debug(f"llm fell back to rule for {f.symbol}")
            base = rule
    else:
        base = rule

    epsilon = float(cfg.explore_epsilon) if cfg is not None else EXPLORE_EPSILON_DEFAULT
    base = maybe_explore(
        base, epsilon=epsilon, roll=explore_rand(),
        asset_class=asset_class, symbol_edge=symbol_edge,
    )

    return await _apply_lessons(base, f, strategy_id)


async def _apply_lessons(
    d: Decision, f: SymbolFeatures, strategy_id: str
) -> Decision:
    """Consult agent_lessons; override hold on confident avoid hits."""
    if d.side == "hold":
        return d
    try:
        hits = await lessons_relevant_to(f, strategy_id, side=d.side)
    except Exception as e:
        logger.warning(f"agent_lessons lookup failed for {f.symbol}: {e}")
        return d
    if not hits:
        return d

    avoid_hit = next(
        (h for h in hits if h.verdict == "avoid" and h.confidence >= LESSON_CONFIDENCE_GATE),
        None,
    )
    if avoid_hit is not None:
        new_thesis = (
            f"LESSON-OVERRIDE → hold | {avoid_hit.pattern_description} "
            f"(n={avoid_hit.n_observations}, conf={avoid_hit.confidence:.2f}) | "
            f"orig: {d.thesis}"
        )
        return Decision(
            symbol=d.symbol,
            side="hold",
            confidence=Decimal("0"),
            thesis=new_thesis[:1000],
            method=f"{d.method}+lesson",
            feature_dump={
                **d.feature_dump,
                "lesson_override": {
                    "lesson_id": avoid_hit.lesson_id,
                    "verdict": "avoid",
                    "confidence": str(avoid_hit.confidence),
                    "n": avoid_hit.n_observations,
                },
            },
            last_price=d.last_price,
        )

    prefer_hit = next(
        (h for h in hits if h.verdict == "prefer" and h.confidence >= LESSON_CONFIDENCE_GATE),
        None,
    )
    if prefer_hit is not None:
        boosted = min(Decimal("1"), d.confidence + Decimal("0.10"))
        return Decision(
            symbol=d.symbol,
            side=d.side,
            confidence=boosted,
            thesis=f"LESSON+ {prefer_hit.pattern_description} | {d.thesis}"[:1000],
            method=f"{d.method}+lesson",
            feature_dump={
                **d.feature_dump,
                "lesson_boost": {
                    "lesson_id": prefer_hit.lesson_id,
                    "verdict": "prefer",
                    "confidence": str(prefer_hit.confidence),
                },
            },
            last_price=d.last_price,
        )

    return d

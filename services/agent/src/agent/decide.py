"""Decision engine: rule-based scoring, with optional LLM override.

Inputs: SymbolFeatures
Outputs: Decision with side, confidence, thesis, full feature context

The rule-based path is deterministic and free to evaluate. The LLM path
adds context-aware reasoning when AI_GATEWAY_API_KEY is configured.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from loguru import logger

from agent.features import SymbolFeatures
from agent.llm import call_llm_decision, llm_enabled

# Rule-based scoring weights — agent's strategy_version increments when
# reflection mutates these.
WEIGHTS: dict[str, Decimal] = {
    "trade_flow": Decimal("0.35"),
    "funding": Decimal("0.20"),
    "oi_delta": Decimal("0.20"),
    "ob_imbalance": Decimal("0.15"),
    "news": Decimal("0.10"),
}

# Threshold for emitting a non-hold decision (rule path)
RULE_SIGNAL_THRESHOLD = Decimal("0.18")


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
    """Naive sentiment: count of bullish/bearish keywords in last-hour titles."""
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


def rule_decide(f: SymbolFeatures, weights: dict[str, Decimal] = WEIGHTS) -> Decision:
    """Linear-combine signals, threshold to decide side."""
    sub = {
        "trade_flow": _trade_flow_score(f),
        "funding": _funding_score(f),
        "oi_delta": _oi_score(f),
        "ob_imbalance": _ob_imbalance_score(f),
        "news": _news_score(f),
    }
    total = sum((weights.get(k, Decimal("0")) * v for k, v in sub.items()), Decimal("0"))

    if total >= RULE_SIGNAL_THRESHOLD:
        side = "long"
        conf = min(Decimal("1"), total)
    elif total <= -RULE_SIGNAL_THRESHOLD:
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


async def decide(f: SymbolFeatures) -> Decision:
    """Top-level decision: rule-based by default; LLM if enabled.

    The LLM result, when present, overrides the rule decision but the rule
    decision is still computed and stored in feature_dump for audit.
    """
    rule = rule_decide(f)
    if not llm_enabled():
        return rule

    llm = await call_llm_decision(_llm_prompt(f))
    if llm is None:
        logger.debug(f"llm fell back to rule for {f.symbol}")
        return rule

    return Decision(
        symbol=f.symbol,
        side=llm.side,
        confidence=Decimal(str(llm.confidence)),
        thesis=f"LLM: {llm.reasoning} || rule: {rule.thesis}",
        method="llm",
        feature_dump={**rule.feature_dump, "llm_confidence": llm.confidence},
        last_price=f.last_price,
    )

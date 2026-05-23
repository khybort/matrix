"""Per-genome decision — applies a Genome's weights/threshold over the
same feature primitives used by services/agent.

This mirrors agent/decide.py rule path but with parameterized weights.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from agent.decide import (
    _funding_score,
    _news_score,
    _ob_imbalance_score,
    _oi_score,
    _trade_flow_score,
)
from agent.features import SymbolFeatures

from labs.genome import Genome


@dataclass(slots=True)
class GenomeDecision:
    symbol: str
    side: str  # long | short | hold
    confidence: Decimal
    score: Decimal
    last_price: Decimal | None


def decide_with_genome(g: Genome, f: SymbolFeatures) -> GenomeDecision:
    sub = {
        "trade_flow": _trade_flow_score(f),
        "funding": _funding_score(f),
        "oi_delta": _oi_score(f),
        "ob_imbalance": _ob_imbalance_score(f),
        "news": _news_score(f),
    }
    total = sum(
        (g.weights.get(k, Decimal("0")) * v for k, v in sub.items()),
        Decimal("0"),
    )

    if total >= g.signal_threshold:
        side = "long"
        confidence = min(Decimal("1"), total)
    elif total <= -g.signal_threshold:
        side = "short"
        confidence = min(Decimal("1"), abs(total))
    else:
        side = "hold"
        confidence = Decimal("0")

    return GenomeDecision(
        symbol=f.symbol,
        side=side,
        confidence=confidence,
        score=total,
        last_price=f.last_price,
    )

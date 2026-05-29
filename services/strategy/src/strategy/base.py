"""Strategy module protocol + shared types."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol


@dataclass(slots=True)
class PredictionDraft:
    """What a strategy module produces — not yet persisted."""

    strategy_id: str
    strategy_version: int
    symbol: str
    exchange: str
    side: str  # long | short | flat
    confidence: Decimal
    horizon_seconds: int
    entry_price_ref: Decimal
    generated_at: datetime
    asset_class: str = "crypto"  # crypto | bist | ...
    thesis: str | None = None
    context: dict[str, Any] = field(default_factory=dict)
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    tp_pct: Decimal | None = None
    sl_pct: Decimal | None = None


class Strategy(Protocol):
    """A strategy module reads recent market state and emits 0..N PredictionDrafts.

    `market` must match a registered `MarketAdapter.name` ("crypto", "bist", ...).
    The strategy dispatcher only invokes a strategy when its market's session
    is open, so modules can drop their own session gating.
    """

    id: str
    version: int
    horizon_seconds: int
    market: str  # "crypto" | "bist" | ...

    async def generate(self) -> Sequence[PredictionDraft]:
        ...

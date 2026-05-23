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
    thesis: str | None = None
    context: dict[str, Any] = field(default_factory=dict)
    id: uuid.UUID = field(default_factory=uuid.uuid4)


class Strategy(Protocol):
    """A strategy module reads recent market state and emits 0..N PredictionDrafts."""

    id: str
    version: int
    horizon_seconds: int

    async def generate(self) -> Sequence[PredictionDraft]:
        ...

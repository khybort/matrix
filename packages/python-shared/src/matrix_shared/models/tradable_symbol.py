"""Dynamic tradable-symbol universe with potential scores.

The universe manager (`services/labs/.../universe.py`) scores a candidate pool
and flips `active` here. SHARED tier — every node's agent and paper engine must
agree on the active set (same reasoning as predictions/wallet).

For crypto this is the runtime source of truth for the tradable universe
(replacing the old hardcoded `_DEFAULT_UNIVERSE`); for BIST `bist_symbols.active`
stays authoritative and this carries the score overlay for uniform observability.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DECIMAL,
    JSON,
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from matrix_shared.models.base import Base, TimestampMixin


class TradableSymbol(Base, TimestampMixin):
    __tablename__ = "tradable_symbols"
    __table_args__ = (
        PrimaryKeyConstraint("asset_class", "symbol"),
        Index("ix_tradable_active", "asset_class", "active"),
        Index("ix_tradable_score", "asset_class", "score"),
    )

    asset_class: Mapped[str] = mapped_column(String(16))
    symbol: Mapped[str] = mapped_column(String(32))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Composite potential score in [0,1]; null until first scored.
    score: Mapped[float | None] = mapped_column(Float)
    # Per-component subscores + raw inputs, for audit/dashboard (like lab metrics).
    components_json: Mapped[dict | None] = mapped_column(JSON)
    # 24h turnover (USD) — the hard min-liquidity floor input.
    liquidity_usd: Mapped[Decimal | None] = mapped_column(DECIMAL(28, 2))
    # Cross-sectional rank within the candidate pool at last scoring.
    rank: Mapped[int | None] = mapped_column(Integer)
    became_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

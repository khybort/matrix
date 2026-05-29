"""Strategy-emitted predictions + their lifecycle."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DECIMAL, JSON, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from matrix_shared.models.base import Base, TimestampMixin


class Prediction(Base, TimestampMixin):
    """One signal emitted by a strategy module at a point in time."""

    __tablename__ = "predictions"
    __table_args__ = (
        Index("ix_predictions_status_strategy", "status", "strategy_id"),
        Index("ix_predictions_symbol_ts", "symbol", "generated_at"),
        Index("ix_predictions_asset_class", "asset_class"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    strategy_id: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_version: Mapped[int] = mapped_column(nullable=False, default=1)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    exchange: Mapped[str] = mapped_column(String(32), nullable=False)
    asset_class: Mapped[str] = mapped_column(
        String(16), nullable=False, default="crypto"
    )
    side: Mapped[str] = mapped_column(String(16), nullable=False)  # long | short | flat | delta_neutral
    confidence: Mapped[Decimal] = mapped_column(DECIMAL(6, 5), nullable=False, default=Decimal("0.5"))
    horizon_seconds: Mapped[int] = mapped_column(nullable=False)
    close_by: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    entry_price_ref: Mapped[Decimal] = mapped_column(DECIMAL(24, 12), nullable=False)
    thesis: Mapped[str | None] = mapped_column(Text)
    context: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="open"
    )  # open | closed | expired
    # Optional per-trade close-early thresholds. NULL → rely on horizon only.
    # See migration 0013 for sign semantics.
    tp_pct: Mapped[Decimal | None] = mapped_column(DECIMAL(8, 6))
    sl_pct: Mapped[Decimal | None] = mapped_column(DECIMAL(8, 6))


class PaperPosition(Base, TimestampMixin):
    """A hypothetical position opened from a prediction in paper-trade mode."""

    __tablename__ = "paper_positions"
    __table_args__ = (
        Index("ix_paper_positions_asset_class", "asset_class"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    wallet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wallets.id", ondelete="CASCADE"),
        nullable=False,
    )
    prediction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("predictions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,  # 1:1 with Prediction in current single-fill model
    )
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    exchange: Mapped[str] = mapped_column(String(32), nullable=False)
    asset_class: Mapped[str] = mapped_column(
        String(16), nullable=False, default="crypto"
    )
    side: Mapped[str] = mapped_column(String(16), nullable=False)
    notional_usd: Mapped[Decimal] = mapped_column(DECIMAL(18, 4), nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    opened_price: Mapped[Decimal] = mapped_column(DECIMAL(24, 12), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_price: Mapped[Decimal | None] = mapped_column(DECIMAL(24, 12))
    pnl_usd: Mapped[Decimal | None] = mapped_column(DECIMAL(18, 6))
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="open"
    )  # open | closed


class Outcome(Base, TimestampMixin):
    """Score of a prediction once its horizon has passed."""

    __tablename__ = "outcomes"
    __table_args__ = (
        Index("ix_outcomes_asset_class", "asset_class"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    prediction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("predictions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    asset_class: Mapped[str] = mapped_column(
        String(16), nullable=False, default="crypto"
    )
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    pnl_usd: Mapped[Decimal] = mapped_column(DECIMAL(18, 6), nullable=False)
    pnl_pct: Mapped[Decimal] = mapped_column(DECIMAL(10, 6), nullable=False)
    score: Mapped[Decimal] = mapped_column(DECIMAL(8, 6), nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

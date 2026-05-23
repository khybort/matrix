"""Ticker snapshots — mark price, index price, funding rate, open interest, etc.

Updated frequently by Bybit's `tickers.<symbol>` topic. We persist on changes
to fields we care about (funding_rate, open_interest), throttled to avoid spam.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DECIMAL, DateTime, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from matrix_shared.models.base import Base, TimestampMixin


class TickerSnapshot(Base, TimestampMixin):
    """Snapshot of derivative metadata at a moment in time."""

    __tablename__ = "market_ticker_snapshots"
    __table_args__ = (
        Index("ix_market_ticker_symbol_ts", "symbol", "snapshot_ts"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    exchange: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    snapshot_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    last_price: Mapped[Decimal | None] = mapped_column(DECIMAL(24, 12))
    mark_price: Mapped[Decimal | None] = mapped_column(DECIMAL(24, 12))
    index_price: Mapped[Decimal | None] = mapped_column(DECIMAL(24, 12))
    funding_rate: Mapped[Decimal | None] = mapped_column(DECIMAL(18, 10))
    next_funding_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    open_interest: Mapped[Decimal | None] = mapped_column(DECIMAL(28, 8))
    volume_24h: Mapped[Decimal | None] = mapped_column(DECIMAL(28, 8))
    turnover_24h: Mapped[Decimal | None] = mapped_column(DECIMAL(28, 8))

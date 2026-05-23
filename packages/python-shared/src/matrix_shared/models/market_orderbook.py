"""Periodic top-of-book snapshots from exchange WS feeds.

We subscribe to a delta-based orderbook stream, maintain the book in memory,
and persist top-N levels at a throttled interval (default every few seconds).
This trades some replay fidelity for vastly less DB write pressure.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from matrix_shared.models.base import Base, TimestampMixin


class OrderBookSnapshot(Base, TimestampMixin):
    """Top-N bid/ask snapshot at a moment in time.

    `bids` and `asks` are stored as JSON arrays of [price_str, size_str] tuples,
    ordered by price (best first). Strings preserve exchange precision.
    """

    __tablename__ = "market_orderbook_snapshots"
    __table_args__ = (
        Index("ix_market_ob_symbol_ts", "symbol", "snapshot_ts"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    exchange: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    snapshot_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    bids: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    asks: Mapped[list[Any]] = mapped_column(JSON, nullable=False)

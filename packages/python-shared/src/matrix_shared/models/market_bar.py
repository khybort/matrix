"""OHLCV bar — bar-based market data (Yahoo Finance and similar).

Used by BIST (Yahoo only gives candles, no tick prints). Other asset classes
can store bar-aggregated data here too. For tick-level data see MarketTrade.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DECIMAL, DateTime, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from matrix_shared.models.base import Base


class MarketBar(Base):
    """One OHLCV candle for (symbol, interval, ts)."""

    __tablename__ = "market_bars"
    # LIST-partitioned on asset_class (migration 0022) so crypto and BIST live
    # in physically separate partitions. Postgres requires the partition key
    # in every unique/PK constraint, hence the composite PK (id, asset_class)
    # and the asset_class-prefixed uniqueness.
    __table_args__ = (
        UniqueConstraint(
            "asset_class", "symbol", "interval", "ts", name="uq_market_bars_class_sit"
        ),
        Index("ix_market_bars_symbol_ts", "symbol", "ts"),
        Index("ix_market_bars_class_interval_ts", "asset_class", "interval", "ts"),
        {"postgresql_partition_by": "LIST (asset_class)"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    asset_class: Mapped[str] = mapped_column(String(16), nullable=False, primary_key=True)
    interval: Mapped[str] = mapped_column(String(8), nullable=False)  # 1m | 5m | 15m | 1h | 1d
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    open: Mapped[Decimal] = mapped_column(DECIMAL(24, 12), nullable=False)
    high: Mapped[Decimal] = mapped_column(DECIMAL(24, 12), nullable=False)
    low: Mapped[Decimal] = mapped_column(DECIMAL(24, 12), nullable=False)
    close: Mapped[Decimal] = mapped_column(DECIMAL(24, 12), nullable=False)
    volume: Mapped[Decimal] = mapped_column(DECIMAL(28, 6), nullable=False, default=Decimal("0"))
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="yfinance")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

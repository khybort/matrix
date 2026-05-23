"""High-frequency market trade prints from exchange WS feeds."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DECIMAL, DateTime, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from matrix_shared.models.base import Base, TimestampMixin


class MarketTrade(Base, TimestampMixin):
    """A single trade print from an exchange.

    Indexed for fast time-windowed queries per symbol.
    """

    __tablename__ = "market_trades"
    __table_args__ = (
        Index("ix_market_trades_symbol_ts", "symbol", "trade_ts"),
        Index("ix_market_trades_exchange_id", "exchange", "exchange_trade_id", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    exchange: Mapped[str] = mapped_column(String(32), nullable=False)
    exchange_trade_id: Mapped[str] = mapped_column(String(128), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    trade_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)  # "buy" / "sell"
    price: Mapped[Decimal] = mapped_column(DECIMAL(24, 12), nullable=False)
    size: Mapped[Decimal] = mapped_column(DECIMAL(24, 12), nullable=False)

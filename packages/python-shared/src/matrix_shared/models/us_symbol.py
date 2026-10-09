"""US equity symbol universe metadata, refreshed periodically.

Symbol format is the plain US ticker (AAPL, MSFT, BRK-B). yfinance accepts
these directly — no exchange suffix, unlike BIST's `.IS`.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from matrix_shared.models.base import Base, TimestampMixin


class UsSymbol(Base, TimestampMixin):
    __tablename__ = "us_symbols"
    __table_args__ = (Index("ix_us_symbols_active", "active"),)

    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(128))
    sector: Mapped[str | None] = mapped_column(String(64))
    exchange: Mapped[str | None] = mapped_column(String(16))  # NASDAQ | NYSE | ...
    index_membership: Mapped[str | None] = mapped_column(String(64))  # e.g. "SP500,NDX"
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

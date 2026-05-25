"""BIST symbol universe metadata, refreshed periodically.

Symbol format here is the BIST ticker without the Yahoo `.IS` suffix
(e.g. THYAO, GARAN). The Yahoo suffix is applied at fetch time.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from matrix_shared.models.base import Base, TimestampMixin


class BistSymbol(Base, TimestampMixin):
    __tablename__ = "bist_symbols"
    __table_args__ = (Index("ix_bist_symbols_active", "active"),)

    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(128))
    sector: Mapped[str | None] = mapped_column(String(64))
    index_membership: Mapped[str | None] = mapped_column(String(64))  # e.g. "BIST30,BIST100"
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

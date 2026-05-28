"""Per-strategy position slot allocation config."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from matrix_shared.models.base import Base


class StrategySlotConfig(Base):
    __tablename__ = "strategy_slot_configs"
    __table_args__ = (
        Index("ix_ssc_wallet", "wallet_id"),
    )

    strategy_id: Mapped[str] = mapped_column(Text(), primary_key=True)
    asset_class: Mapped[str] = mapped_column(Text(), primary_key=True)
    wallet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wallets.id", ondelete="CASCADE"),
        primary_key=True,
    )
    allocated_slots: Mapped[int] = mapped_column(Integer(), nullable=False, default=1)
    perf_score: Mapped[float] = mapped_column(Float(), nullable=False, default=0.5)
    consecutive_losses: Mapped[int] = mapped_column(Integer(), nullable=False, default=0)
    last_evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=lambda: datetime.now(UTC),
    )

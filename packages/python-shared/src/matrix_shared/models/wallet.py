"""Virtual wallet (paper-trade capital tracking) + equity snapshots.

A `Wallet` row represents a sandboxed capital pool with risk caps. Multiple
wallets can exist (e.g. one per strategy version under test, or one global).
The paper-trade engine reserves capital on open and credits PnL on close.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DECIMAL, DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from matrix_shared.models.base import Base, TimestampMixin


class Wallet(Base, TimestampMixin):
    """A paper-trade capital pool with risk caps.

    Invariant: equity = cash + sum(open_position_notional * mark_price_factor).
    For our simple model we keep `cash` as the realized balance and recompute
    equity on the fly from open positions' marked PnL.
    """

    __tablename__ = "wallets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    starting_capital_usd: Mapped[Decimal] = mapped_column(
        DECIMAL(18, 4), nullable=False, default=Decimal("10000")
    )
    cash_usd: Mapped[Decimal] = mapped_column(
        DECIMAL(18, 6), nullable=False, default=Decimal("10000")
    )
    locked_usd: Mapped[Decimal] = mapped_column(
        DECIMAL(18, 6), nullable=False, default=Decimal("0")
    )

    # Risk caps — read by paper-trade engine. Never mutated by reflection agent.
    max_position_pct: Mapped[Decimal] = mapped_column(
        DECIMAL(6, 4), nullable=False, default=Decimal("0.02")
    )  # 2% per single position by default
    max_concurrent_positions: Mapped[int] = mapped_column(nullable=False, default=5)
    daily_loss_circuit_pct: Mapped[Decimal] = mapped_column(
        DECIMAL(6, 4), nullable=False, default=Decimal("0.05")
    )  # 5% daily loss = circuit breaker

    # Circuit breaker state
    circuit_tripped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    day_start_equity: Mapped[Decimal] = mapped_column(
        DECIMAL(18, 6), nullable=False, default=Decimal("10000")
    )
    day_start_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )


class WalletSnapshot(Base, TimestampMixin):
    """Equity curve — one row per snapshot, taken by paper-trade engine each tick."""

    __tablename__ = "wallet_snapshots"
    __table_args__ = (
        Index("ix_wallet_snapshots_wallet_ts", "wallet_id", "snapshot_ts"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    wallet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wallets.id", ondelete="CASCADE"),
        nullable=False,
    )
    snapshot_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    equity_usd: Mapped[Decimal] = mapped_column(DECIMAL(18, 6), nullable=False)
    cash_usd: Mapped[Decimal] = mapped_column(DECIMAL(18, 6), nullable=False)
    locked_usd: Mapped[Decimal] = mapped_column(DECIMAL(18, 6), nullable=False)
    n_open_positions: Mapped[int] = mapped_column(nullable=False, default=0)
    realized_pnl_usd: Mapped[Decimal] = mapped_column(
        DECIMAL(18, 6), nullable=False, default=Decimal("0")
    )
    unrealized_pnl_usd: Mapped[Decimal] = mapped_column(
        DECIMAL(18, 6), nullable=False, default=Decimal("0")
    )

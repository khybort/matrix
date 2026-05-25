"""Paper-trade certificate — the Phase 5 live-execution gate.

A strategy/version cannot graduate to live capital without a 'granted'
certificate row whose validity has not expired. The check is enforced
in matrix_shared.trading_safety.has_valid_certificate; execution code
(once it exists) must consult it before submitting any live order.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DECIMAL, CheckConstraint, DateTime, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from matrix_shared.models.base import Base, TimestampMixin


class PaperTradeCertificate(Base, TimestampMixin):
    """One certificate per (strategy_id, asset_class, version).

    Status flow:
        pending → granted → (revoked | expired)

    `validity_until` is the soft expiry. Certs without an explicit expiry
    are accepted but discouraged — paper performance drifts, and re-grant
    cycles are how the system catches regression.
    """

    __tablename__ = "paper_trade_certificate"
    __table_args__ = (
        UniqueConstraint(
            "strategy_id", "asset_class", "version",
            name="uq_paper_trade_cert",
        ),
        CheckConstraint(
            "status IN ('pending', 'granted', 'revoked', 'expired')",
            name="ck_paper_trade_cert_status",
        ),
        Index("ix_paper_trade_cert_status", "status"),
        Index("ix_paper_trade_cert_strategy", "strategy_id", "asset_class", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    strategy_id: Mapped[str] = mapped_column(String(64), nullable=False)
    asset_class: Mapped[str] = mapped_column(
        String(16), nullable=False, default="crypto"
    )
    version: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending"
    )

    # Evidence snapshot captured at grant time.
    n_outcomes: Mapped[int] = mapped_column(nullable=False, default=0)
    observation_days: Mapped[int] = mapped_column(nullable=False, default=0)
    win_rate: Mapped[Decimal | None] = mapped_column(DECIMAL(8, 6))
    avg_pnl_usd: Mapped[Decimal | None] = mapped_column(DECIMAL(18, 6))
    total_pnl_usd: Mapped[Decimal | None] = mapped_column(DECIMAL(18, 6))
    max_drawdown_pct: Mapped[Decimal | None] = mapped_column(DECIMAL(8, 6))
    sharpe_ratio: Mapped[Decimal | None] = mapped_column(DECIMAL(8, 4))

    # Lifecycle
    granted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    granted_by: Mapped[str | None] = mapped_column(String(64))
    validity_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(Text)

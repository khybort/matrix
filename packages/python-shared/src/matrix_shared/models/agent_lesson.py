"""Per-decision lesson learned from matrix_agent's own outcome history.

One row = one detected pattern. Status flow:
  active     → currently informing decisions
  superseded → newer aggregation of the same pattern_kind replaced it
  expired    → pattern fell out of the rolling window

`pattern_filter` is a JSON predicate the agent matches against current
SymbolFeatures. Shape varies by `pattern_kind` (see services/agent_lessons
for the recognised kinds).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    DECIMAL,
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from matrix_shared.models.base import Base, TimestampMixin


class AgentLesson(Base, TimestampMixin):
    __tablename__ = "agent_lessons"
    __table_args__ = (
        Index("ix_agent_lessons_active", "strategy_id", "status", "pattern_kind"),
        Index("ix_agent_lessons_generated", "generated_at"),
        CheckConstraint(
            "verdict IN ('avoid', 'prefer', 'neutral')",
            name="ck_agent_lessons_verdict",
        ),
        CheckConstraint(
            "status IN ('active', 'superseded', 'expired')",
            name="ck_agent_lessons_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    strategy_id: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_version: Mapped[int] = mapped_column(nullable=False)
    pattern_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    pattern_description: Mapped[str] = mapped_column(Text, nullable=False)
    pattern_filter: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    n_observations: Mapped[int] = mapped_column(nullable=False)
    win_rate: Mapped[Decimal | None] = mapped_column(DECIMAL(8, 6))
    avg_pnl_usd: Mapped[Decimal | None] = mapped_column(DECIMAL(18, 6))
    total_pnl_usd: Mapped[Decimal | None] = mapped_column(DECIMAL(18, 6))
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(DECIMAL(6, 4))
    observed_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    observed_until: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active"
    )
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_lessons.id")
    )

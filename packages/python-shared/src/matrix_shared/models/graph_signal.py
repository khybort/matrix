"""Federated graph context aggregate (SHARED tier).

Each PC publishes a snapshot of its local AGE-derived asset context here
periodically; agents on other PCs (or this PC, when its local graph is
sparse for an asset) read the latest row.

See docs/MULTI_PC_SETUP.md for the federated-learning rationale.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DECIMAL, JSON, DateTime, Float, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from matrix_shared.models.base import Base, TimestampMixin


class GraphSignal(Base, TimestampMixin):
    __tablename__ = "graph_signals"
    __table_args__ = (
        Index("ix_graph_signals_asset_ts", "asset", "computed_at"),
        Index("ix_graph_signals_node_ts", "node_id", "computed_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    node_id: Mapped[str] = mapped_column(String(128), nullable=False)
    asset: Mapped[str] = mapped_column(String(32), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_hours: Mapped[float] = mapped_column(Float, nullable=False, default=24.0)

    direct_mention_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    recency_weight: Mapped[Decimal] = mapped_column(
        DECIMAL(18, 6), nullable=False, default=Decimal("0")
    )
    direct_polarity: Mapped[Decimal] = mapped_column(
        DECIMAL(8, 6), nullable=False, default=Decimal("0")
    )
    contextual_polarity: Mapped[Decimal] = mapped_column(
        DECIMAL(8, 6), nullable=False, default=Decimal("0")
    )
    n_contextual_documents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    related_companies: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    co_mentioned_assets: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)

    # Health telemetry — how long did the local AGE query take
    computed_in_ms: Mapped[int | None] = mapped_column(Integer)

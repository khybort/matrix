"""Lab experiments — evolutionary algorithm search over agent parameters.

`LabExperiment` is a single genome (parameter set) being evaluated.
`LabEvaluation` is one hypothetical trade: at generated_at the genome
emitted a signal; at close_at we mark its outcome from observed prices.

This entire substrate runs **separately from the wallet/paper-trade engine**.
Evaluations are purely computational — no real or paper money flows.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DECIMAL, JSON, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from matrix_shared.models.base import Base, TimestampMixin


class LabExperiment(Base, TimestampMixin):
    """One genome (parameter set) in the evolutionary search."""

    __tablename__ = "lab_experiments"
    __table_args__ = (
        Index("ix_lab_experiments_gen_status", "generation", "status"),
        Index("ix_lab_experiments_fitness", "fitness_score"),
        Index("ix_lab_experiments_asset_class", "asset_class"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    asset_class: Mapped[str] = mapped_column(
        String(16), nullable=False, default="crypto"
    )
    generation: Mapped[int] = mapped_column(nullable=False, default=0)
    parent_a_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("lab_experiments.id", ondelete="SET NULL")
    )
    parent_b_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("lab_experiments.id", ondelete="SET NULL")
    )
    params: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    rationale: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active"
    )  # active | retired | promoted
    # rolling fitness aggregates — updated as evaluations close
    n_evaluations: Mapped[int] = mapped_column(nullable=False, default=0)
    n_signals: Mapped[int] = mapped_column(nullable=False, default=0)  # # of times it emitted non-hold
    n_wins: Mapped[int] = mapped_column(nullable=False, default=0)
    total_score: Mapped[Decimal] = mapped_column(
        DECIMAL(18, 6), nullable=False, default=Decimal("0")
    )
    fitness_score: Mapped[Decimal] = mapped_column(
        DECIMAL(18, 6), nullable=False, default=Decimal("0")
    )
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LabEvaluation(Base, TimestampMixin):
    """One hypothetical trade emitted by a LabExperiment.

    Lifecycle:
        open: created when a genome's decision threshold is crossed
        scored: at close_at, observed price is fetched, pnl/score computed
    """

    __tablename__ = "lab_evaluations"
    __table_args__ = (
        Index("ix_lab_evaluations_open", "status", "close_at"),
        Index("ix_lab_evaluations_experiment", "experiment_id", "status"),
        Index("ix_lab_evaluations_asset_class", "asset_class"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("lab_experiments.id", ondelete="CASCADE"),
        nullable=False,
    )
    asset_class: Mapped[str] = mapped_column(
        String(16), nullable=False, default="crypto"
    )
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(DECIMAL(6, 5), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    close_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(DECIMAL(24, 12), nullable=False)
    exit_price: Mapped[Decimal | None] = mapped_column(DECIMAL(24, 12))
    pnl_pct: Mapped[Decimal | None] = mapped_column(DECIMAL(10, 6))
    score: Mapped[Decimal | None] = mapped_column(DECIMAL(8, 6))
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="open"
    )  # open | scored | stale (no price at close_at)

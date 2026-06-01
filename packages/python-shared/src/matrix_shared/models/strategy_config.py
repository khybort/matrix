"""Versioned strategy/agent configurations + mutation proposals.

Active config drives the running agent. Reflection proposes shadow configs
based on outcome scoring. Promotion shadow→active is gated by elapsed time
and a positive risk-adjusted score delta (TBD — for now manual).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from matrix_shared.models.base import Base, TimestampMixin


class StrategyConfig(Base, TimestampMixin):
    """A versioned parameter snapshot for a strategy or agent."""

    __tablename__ = "strategy_configs"
    __table_args__ = (
        UniqueConstraint(
            "strategy_id", "asset_class", "version", name="uq_strategy_configs_id_class_ver"
        ),
        Index("ix_strategy_configs_status", "strategy_id", "status"),
        Index("ix_strategy_configs_asset_class", "asset_class"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    strategy_id: Mapped[str] = mapped_column(String(64), nullable=False)
    asset_class: Mapped[str] = mapped_column(
        String(16), nullable=False, default="crypto"
    )
    version: Mapped[int] = mapped_column(nullable=False, default=1)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active"
    )  # active | shadow | retired
    params: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    rationale: Mapped[str | None] = mapped_column(Text)
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MutationProposal(Base, TimestampMixin):
    """Reflection agent's proposal to mutate a strategy/agent config.

    A proposal becomes a real StrategyConfig once accepted. Until then, this
    is an auditable record of what reflection suggested and why.
    """

    __tablename__ = "mutation_proposals"
    __table_args__ = (
        Index("ix_mutation_proposals_strategy", "strategy_id", "status"),
        Index("ix_mutation_proposals_asset_class", "asset_class"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    strategy_id: Mapped[str] = mapped_column(String(64), nullable=False)
    asset_class: Mapped[str] = mapped_column(
        String(16), nullable=False, default="crypto"
    )
    from_version: Mapped[int] = mapped_column(nullable=False)
    to_version: Mapped[int] = mapped_column(nullable=False)
    proposal_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # "weight_tune" | "threshold_change" | "retire" | "prompt_change"
    before_params: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    after_params: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    metrics_window: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending"
    )  # pending | applied | rejected
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="rule"
    )  # rule | llm

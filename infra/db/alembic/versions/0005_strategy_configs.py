"""strategy_configs + mutation_proposals; seed default agent config

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-23

"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_AGENT_CONFIG_ID = uuid.UUID("00000000-0000-0000-0000-00000000a6e1")


def upgrade() -> None:
    op.create_table(
        "strategy_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("strategy_id", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("params", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("rationale", sa.Text()),
        sa.Column("promoted_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("strategy_id", "version", name="uq_strategy_configs_id_ver"),
    )
    op.create_index("ix_strategy_configs_status", "strategy_configs", ["strategy_id", "status"])

    op.create_table(
        "mutation_proposals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("strategy_id", sa.String(64), nullable=False),
        sa.Column("from_version", sa.Integer(), nullable=False),
        sa.Column("to_version", sa.Integer(), nullable=False),
        sa.Column("proposal_type", sa.String(32), nullable=False),
        sa.Column("before_params", sa.JSON(), nullable=False),
        sa.Column("after_params", sa.JSON(), nullable=False),
        sa.Column("metrics_window", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("applied_at", sa.DateTime(timezone=True)),
        sa.Column("source", sa.String(16), nullable=False, server_default="rule"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_mutation_proposals_strategy", "mutation_proposals", ["strategy_id", "status"]
    )

    # Seed initial matrix_agent config (mirrors hardcoded defaults in agent/decide.py)
    op.execute(
        sa.text(
            "INSERT INTO strategy_configs (id, strategy_id, version, status, params, rationale) "
            "VALUES (:id, :sid, 1, 'active', :params, :rationale) "
            "ON CONFLICT DO NOTHING"
        ).bindparams(
            id=DEFAULT_AGENT_CONFIG_ID,
            sid="matrix_agent",
            params=json.dumps({
                "weights": {
                    "trade_flow": "0.35",
                    "funding": "0.20",
                    "oi_delta": "0.20",
                    "ob_imbalance": "0.15",
                    "news": "0.10",
                },
                "signal_threshold": "0.18",
            }),
            rationale="initial seed; hand-tuned starting weights",
        )
    )


def downgrade() -> None:
    op.drop_index("ix_mutation_proposals_strategy", table_name="mutation_proposals")
    op.drop_table("mutation_proposals")
    op.drop_index("ix_strategy_configs_status", table_name="strategy_configs")
    op.drop_table("strategy_configs")

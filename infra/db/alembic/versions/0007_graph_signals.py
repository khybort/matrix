"""graph_signals — federated AGE context aggregates published to SHARED tier

Each PC computes asset context from its LOCAL AGE matrix_graph and periodically
publishes a denormalized snapshot here. Other PCs (and the agent) can fall
back to these aggregates when their own local graph has thin coverage of an
asset. The pattern is federated-learning-style: each node enriches autonomously,
shares only aggregates, never raw documents.

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-24

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "graph_signals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("node_id", sa.String(128), nullable=False),
        sa.Column("asset", sa.String(32), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_hours", sa.Float(), nullable=False, server_default="24.0"),
        sa.Column("direct_mention_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("recency_weight", sa.DECIMAL(18, 6), nullable=False, server_default="0"),
        sa.Column("direct_polarity", sa.DECIMAL(8, 6), nullable=False, server_default="0"),
        sa.Column("contextual_polarity", sa.DECIMAL(8, 6), nullable=False, server_default="0"),
        sa.Column("n_contextual_documents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("related_companies", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("co_mentioned_assets", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("computed_in_ms", sa.Integer()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    # Most-recent-per-asset query: latest row per asset across all nodes
    op.create_index(
        "ix_graph_signals_asset_ts",
        "graph_signals",
        ["asset", "computed_at"],
        postgresql_ops={"computed_at": "DESC"},
    )
    # Per-node history (debug / health)
    op.create_index(
        "ix_graph_signals_node_ts",
        "graph_signals",
        ["node_id", "computed_at"],
        postgresql_ops={"computed_at": "DESC"},
    )


def downgrade() -> None:
    op.drop_index("ix_graph_signals_node_ts", table_name="graph_signals")
    op.drop_index("ix_graph_signals_asset_ts", table_name="graph_signals")
    op.drop_table("graph_signals")

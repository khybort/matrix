"""screener_signals table — deterministic opportunity discovery

Revision ID: 0023
Revises: 0022
Create Date: 2026-05-28
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "screener_signals",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("signal_type", sa.String(32), nullable=False),
        sa.Column("funding_rate", sa.Numeric(12, 8), nullable=True),
        sa.Column("oi_value", sa.Numeric(24, 2), nullable=True),
        sa.Column("score", sa.Numeric(12, 8), nullable=False),
        sa.Column("passes", sa.Integer, nullable=False, server_default="1"),
        sa.Column("status", sa.String(16), nullable=False, server_default="signal"),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("symbol", "signal_type", name="uq_screener_signals"),
    )
    op.create_index("ix_screener_signals_type", "screener_signals", ["signal_type"])
    op.create_index("ix_screener_signals_score", "screener_signals", ["score"])
    op.create_index("ix_screener_signals_status", "screener_signals", ["status", "observed_at"])


def downgrade() -> None:
    op.drop_table("screener_signals")

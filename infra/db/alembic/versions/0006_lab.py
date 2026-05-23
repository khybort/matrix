"""lab_experiments + lab_evaluations (evolutionary algorithm search)

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-23

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lab_experiments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "parent_a_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("lab_experiments.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "parent_b_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("lab_experiments.id", ondelete="SET NULL"),
        ),
        sa.Column("params", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("rationale", sa.Text()),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("n_evaluations", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_signals", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_wins", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_score", sa.DECIMAL(18, 6), nullable=False, server_default="0"),
        sa.Column("fitness_score", sa.DECIMAL(18, 6), nullable=False, server_default="0"),
        sa.Column("retired_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_lab_experiments_gen_status", "lab_experiments", ["generation", "status"])
    op.create_index("ix_lab_experiments_fitness", "lab_experiments", ["fitness_score"])

    op.create_table(
        "lab_evaluations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "experiment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("lab_experiments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("confidence", sa.DECIMAL(6, 5), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("close_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entry_price", sa.DECIMAL(24, 12), nullable=False),
        sa.Column("exit_price", sa.DECIMAL(24, 12)),
        sa.Column("pnl_pct", sa.DECIMAL(10, 6)),
        sa.Column("score", sa.DECIMAL(8, 6)),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_lab_evaluations_open", "lab_evaluations", ["status", "close_at"])
    op.create_index("ix_lab_evaluations_experiment", "lab_evaluations", ["experiment_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_lab_evaluations_experiment", table_name="lab_evaluations")
    op.drop_index("ix_lab_evaluations_open", table_name="lab_evaluations")
    op.drop_table("lab_evaluations")
    op.drop_index("ix_lab_experiments_fitness", table_name="lab_experiments")
    op.drop_index("ix_lab_experiments_gen_status", table_name="lab_experiments")
    op.drop_table("lab_experiments")

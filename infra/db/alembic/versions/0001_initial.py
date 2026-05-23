"""initial schema: raw_documents, market_trades, jobs

Revision ID: 0001
Revises:
Create Date: 2026-05-23

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "raw_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("external_id", sa.String(256), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("url", sa.Text()),
        sa.Column("title", sa.Text()),
        sa.Column("body", sa.Text()),
        sa.Column("meta", sa.JSON(), nullable=False, server_default="{}"),
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
        sa.UniqueConstraint("source", "external_id", name="uq_raw_documents_source_ext"),
    )
    op.create_index(
        "ix_raw_documents_source", "raw_documents", ["source"]
    )
    op.create_index(
        "ix_raw_documents_published_at", "raw_documents", ["published_at"]
    )

    op.create_table(
        "market_trades",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("exchange", sa.String(32), nullable=False),
        sa.Column("exchange_trade_id", sa.String(128), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("trade_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("price", sa.DECIMAL(24, 12), nullable=False),
        sa.Column("size", sa.DECIMAL(24, 12), nullable=False),
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
    op.create_index(
        "ix_market_trades_symbol_ts", "market_trades", ["symbol", "trade_ts"]
    )
    op.create_index(
        "ix_market_trades_exchange_id",
        "market_trades",
        ["exchange", "exchange_trade_id"],
        unique=True,
    )

    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("claimed_by", sa.String(128)),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.Text()),
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
    op.create_index("ix_jobs_role_status", "jobs", ["role", "status"])


def downgrade() -> None:
    op.drop_index("ix_jobs_role_status", table_name="jobs")
    op.drop_table("jobs")
    op.drop_index("ix_market_trades_exchange_id", table_name="market_trades")
    op.drop_index("ix_market_trades_symbol_ts", table_name="market_trades")
    op.drop_table("market_trades")
    op.drop_index("ix_raw_documents_published_at", table_name="raw_documents")
    op.drop_index("ix_raw_documents_source", table_name="raw_documents")
    op.drop_table("raw_documents")

"""asset_class column on trading tables; market_bars + bist_symbols

Adds an `asset_class` discriminator (default 'crypto') to every table that
records trading state (predictions, paper_positions, outcomes,
strategy_configs, lab_experiments, lab_evaluations, mutation_proposals).
This is what lets BIST run in parallel to the crypto loop without
collisions in queries, configs, or lab populations.

`market_bars` is the BIST-side equivalent of `market_trades` — bar-based
because Yahoo gives candles, not tick prints. `bist_symbols` carries the
universe metadata refreshed once a day.

String column (not enum) is intentional: adding new asset_class values
later (forex, commodity, etc.) shouldn't require an enum migration.

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-25

"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Stable id so make/scripts can target the row directly.
DEFAULT_BIST_AGENT_CONFIG_ID = uuid.UUID("00000000-0000-0000-0000-00000000b157")


_ASSET_CLASS_TABLES = (
    "predictions",
    "paper_positions",
    "outcomes",
    "strategy_configs",
    "lab_experiments",
    "lab_evaluations",
    "mutation_proposals",
)


def upgrade() -> None:
    for table in _ASSET_CLASS_TABLES:
        op.add_column(
            table,
            sa.Column(
                "asset_class",
                sa.String(16),
                nullable=False,
                server_default="crypto",
            ),
        )
        op.create_index(
            f"ix_{table}_asset_class",
            table,
            ["asset_class"],
        )

    op.create_table(
        "bist_symbols",
        sa.Column("symbol", sa.String(16), primary_key=True),
        sa.Column("name", sa.String(128)),
        sa.Column("sector", sa.String(64)),
        sa.Column("index_membership", sa.String(64)),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_refreshed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_bist_symbols_active", "bist_symbols", ["active"])

    op.create_table(
        "market_bars",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("asset_class", sa.String(16), nullable=False),
        sa.Column("interval", sa.String(8), nullable=False),  # 1m, 5m, 15m, 1h, 1d
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.DECIMAL(24, 12), nullable=False),
        sa.Column("high", sa.DECIMAL(24, 12), nullable=False),
        sa.Column("low", sa.DECIMAL(24, 12), nullable=False),
        sa.Column("close", sa.DECIMAL(24, 12), nullable=False),
        sa.Column("volume", sa.DECIMAL(28, 6), nullable=False, server_default="0"),
        sa.Column("source", sa.String(32), nullable=False, server_default="yfinance"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("symbol", "interval", "ts", name="uq_market_bars_symbol_interval_ts"),
    )
    op.create_index(
        "ix_market_bars_symbol_ts",
        "market_bars",
        ["symbol", "ts"],
        postgresql_ops={"ts": "DESC"},
    )
    op.create_index(
        "ix_market_bars_class_interval_ts",
        "market_bars",
        ["asset_class", "interval", "ts"],
        postgresql_ops={"ts": "DESC"},
    )

    # Seed the BIST-side default agent config. Crypto-only features (funding,
    # oi_delta) are zero so the rule decider never weights them for BIST even
    # if the agent code is misconfigured. Reflection/labs will mutate this
    # config over time the same way they mutate the crypto one.
    op.execute(
        sa.text(
            "INSERT INTO strategy_configs "
            "(id, strategy_id, asset_class, version, status, params, rationale) "
            "VALUES (:id, :sid, 'bist', 1, 'active', :params, :rationale)"
        ).bindparams(
            id=DEFAULT_BIST_AGENT_CONFIG_ID,
            sid="matrix_agent",
            params=json.dumps({
                "weights": {
                    "trade_flow": "0.45",
                    "funding": "0",
                    "oi_delta": "0",
                    "ob_imbalance": "0.20",
                    "news": "0.35",
                },
                "signal_threshold": "0.18",
            }),
            rationale="initial BIST seed; crypto-only signals zeroed",
        )
    )


def downgrade() -> None:
    op.drop_index("ix_market_bars_class_interval_ts", table_name="market_bars")
    op.drop_index("ix_market_bars_symbol_ts", table_name="market_bars")
    op.drop_table("market_bars")
    op.drop_index("ix_bist_symbols_active", table_name="bist_symbols")
    op.drop_table("bist_symbols")
    for table in reversed(_ASSET_CLASS_TABLES):
        op.drop_index(f"ix_{table}_asset_class", table_name=table)
        op.drop_column(table, "asset_class")

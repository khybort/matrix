"""US equities market: us_symbols, market_bars_us partition, wallet + agent seed

Adds the US cash-equity market (asset_class='us') alongside crypto and BIST:
  - `us_symbols` universe table (mirrors bist_symbols, plus an `exchange` col)
  - a `market_bars_us` LIST partition on the partitioned `market_bars` table
  - a default US wallet (short-allowed, T+1, liquid intraday → tighter DD than
    BIST but more concurrent slots)
  - the US-side `matrix_agent` strategy_config seed (crypto-only signals zeroed)

Idempotent where it matters (wallet + config use ON CONFLICT). The partition
is created via detach/attach of the DEFAULT partition so Postgres doesn't have
to re-scan it under load.

Revision ID: 0038
Revises: 0037
Create Date: 2026-06-02
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Stable id so make/scripts can target the row directly (mirrors the BIST 0008 seed).
DEFAULT_US_AGENT_CONFIG_ID = uuid.UUID("00000000-0000-0000-0000-0000000000a5")


def upgrade() -> None:
    # 1) us_symbols universe table -----------------------------------------
    op.create_table(
        "us_symbols",
        sa.Column("symbol", sa.String(16), primary_key=True),
        sa.Column("name", sa.String(128)),
        sa.Column("sector", sa.String(64)),
        sa.Column("exchange", sa.String(16)),
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
    op.create_index("ix_us_symbols_active", "us_symbols", ["active"])

    # 2) market_bars_us partition ------------------------------------------
    # Detach DEFAULT so the new partition can be added without a full re-scan,
    # then reattach. No 'us' rows exist yet, so attach validation is trivial.
    op.execute("ALTER TABLE market_bars DETACH PARTITION market_bars_other")
    op.execute("CREATE TABLE market_bars_us PARTITION OF market_bars FOR VALUES IN ('us')")
    op.execute("ALTER TABLE market_bars ATTACH PARTITION market_bars_other DEFAULT")

    # 3) default US wallet --------------------------------------------------
    op.execute(
        """
        INSERT INTO wallets (
            id, name, asset_class, starting_capital_usd, cash_usd, locked_usd,
            max_position_pct, max_concurrent_positions, daily_loss_circuit_pct,
            equity_trailing_stop_pct, day_start_equity, day_start_at,
            created_at, updated_at
        ) VALUES (
            gen_random_uuid(), 'default', 'us',
            10000, 10000, 0,
            0.03, 5, 0.06,
            0.12, 10000, now(),
            now(), now()
        )
        ON CONFLICT (name, asset_class) DO NOTHING
        """
    )

    # 4) US matrix_agent strategy_config seed ------------------------------
    # No funding/OI on equities; no live orderbook from yfinance bars → those
    # signals are zeroed. trade_flow (bar momentum) + news carry the weight.
    op.execute(
        sa.text(
            "INSERT INTO strategy_configs "
            "(id, strategy_id, asset_class, version, status, params, rationale) "
            "VALUES (:id, :sid, 'us', 1, 'active', :params, :rationale) "
            "ON CONFLICT DO NOTHING"
        ).bindparams(
            id=DEFAULT_US_AGENT_CONFIG_ID,
            sid="matrix_agent",
            params=json.dumps(
                {
                    "weights": {
                        "trade_flow": "0.50",
                        "funding": "0",
                        "oi_delta": "0",
                        "ob_imbalance": "0.15",
                        "news": "0.35",
                    },
                    "signal_threshold": "0.18",
                }
            ),
            rationale="initial US seed; crypto-only signals zeroed",
        )
    )


def downgrade() -> None:
    op.execute("DELETE FROM strategy_configs WHERE strategy_id='matrix_agent' AND asset_class='us'")
    op.execute("DELETE FROM wallets WHERE name='default' AND asset_class='us'")
    # Drop the us partition (rows go with it). DEFAULT stays attached.
    op.execute("DROP TABLE IF EXISTS market_bars_us")
    op.drop_index("ix_us_symbols_active", table_name="us_symbols")
    op.drop_table("us_symbols")

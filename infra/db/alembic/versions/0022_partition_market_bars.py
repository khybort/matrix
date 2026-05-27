"""partition market_bars by asset_class (crypto / bist physically separated)

Operator: "BIST ile bitcoin verileri aynı tablolarda — ayrı olacaktı." We
keep one logical `market_bars` table but make it LIST-partitioned on
asset_class, so crypto and BIST rows live in physically separate partitions
(market_bars_crypto / market_bars_bist) while every existing query keeps
working unchanged.

Postgres requires the partition key in every unique/PK constraint, so:
  - PRIMARY KEY becomes (id, asset_class)
  - the (symbol, interval, ts) uniqueness becomes
    (asset_class, symbol, interval, ts) — named uq_market_bars_class_sit

A regular table can't be converted in place, so we rename the old table,
create the partitioned one, copy rows, and drop the old. Crypto and BIST
never share a symbol, so widening the unique key changes no dedup behavior.

Revision ID: 0022
Revises: 0021
Create Date: 2026-05-27
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLS = (
    "id, symbol, asset_class, interval, ts, open, high, low, close, "
    "volume, source, created_at"
)


def _create_partitioned(table: str) -> None:
    op.execute(f"""
        CREATE TABLE {table} (
          id          uuid          NOT NULL DEFAULT gen_random_uuid(),
          symbol      varchar(32)   NOT NULL,
          asset_class varchar(16)   NOT NULL,
          interval    varchar(8)    NOT NULL,
          ts          timestamptz   NOT NULL,
          open        numeric(24,12) NOT NULL,
          high        numeric(24,12) NOT NULL,
          low         numeric(24,12) NOT NULL,
          close       numeric(24,12) NOT NULL,
          volume      numeric(28,6) NOT NULL DEFAULT 0,
          source      varchar(32)   NOT NULL DEFAULT 'yfinance',
          created_at  timestamptz   NOT NULL DEFAULT NOW(),
          PRIMARY KEY (id, asset_class),
          CONSTRAINT uq_market_bars_class_sit
            UNIQUE (asset_class, symbol, interval, ts)
        ) PARTITION BY LIST (asset_class)
    """)
    op.execute(f"CREATE TABLE {table}_crypto PARTITION OF {table} FOR VALUES IN ('crypto')")
    op.execute(f"CREATE TABLE {table}_bist   PARTITION OF {table} FOR VALUES IN ('bist')")
    op.execute(f"CREATE TABLE {table}_other  PARTITION OF {table} DEFAULT")
    op.execute(f"CREATE INDEX ix_market_bars_symbol_ts ON {table} (symbol, ts)")
    op.execute(
        f"CREATE INDEX ix_market_bars_class_interval_ts ON {table} (asset_class, interval, ts)"
    )


def upgrade() -> None:
    # Move the existing table + its indexes out of the way (index names are
    # schema-global, so the new partitioned table can't reuse them yet).
    op.execute("ALTER TABLE market_bars RENAME TO market_bars_legacy")
    op.execute("ALTER INDEX ix_market_bars_symbol_ts RENAME TO ix_mb_legacy_symbol_ts")
    op.execute(
        "ALTER INDEX ix_market_bars_class_interval_ts RENAME TO ix_mb_legacy_class_interval_ts"
    )

    _create_partitioned("market_bars")
    op.execute(f"INSERT INTO market_bars ({_COLS}) SELECT {_COLS} FROM market_bars_legacy")
    op.execute("DROP TABLE market_bars_legacy")


def downgrade() -> None:
    op.execute("ALTER TABLE market_bars RENAME TO market_bars_part")
    op.execute("ALTER INDEX ix_market_bars_symbol_ts RENAME TO ix_mb_part_symbol_ts")
    op.execute(
        "ALTER INDEX ix_market_bars_class_interval_ts RENAME TO ix_mb_part_class_interval_ts"
    )
    op.execute("""
        CREATE TABLE market_bars (
          id          uuid          NOT NULL DEFAULT gen_random_uuid(),
          symbol      varchar(32)   NOT NULL,
          asset_class varchar(16)   NOT NULL,
          interval    varchar(8)    NOT NULL,
          ts          timestamptz   NOT NULL,
          open        numeric(24,12) NOT NULL,
          high        numeric(24,12) NOT NULL,
          low         numeric(24,12) NOT NULL,
          close       numeric(24,12) NOT NULL,
          volume      numeric(28,6) NOT NULL DEFAULT 0,
          source      varchar(32)   NOT NULL DEFAULT 'yfinance',
          created_at  timestamptz   NOT NULL DEFAULT NOW(),
          PRIMARY KEY (id),
          CONSTRAINT uq_market_bars_symbol_interval_ts UNIQUE (symbol, interval, ts)
        )
    """)
    op.execute("CREATE INDEX ix_market_bars_symbol_ts ON market_bars (symbol, ts)")
    op.execute(
        "CREATE INDEX ix_market_bars_class_interval_ts ON market_bars (asset_class, interval, ts)"
    )
    op.execute(f"INSERT INTO market_bars ({_COLS}) SELECT {_COLS} FROM market_bars_part")
    op.execute("DROP TABLE market_bars_part")

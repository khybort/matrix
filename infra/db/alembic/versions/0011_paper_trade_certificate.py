"""paper_trade_certificate — the Phase 5 live-execution gate

The promise in docs/TRADING.md is "a strategy cannot graduate to live
execution without a paper_trade_certificate row." That promise had no
backing table until now, so execution code (whenever it's built) would
have no DB-level wall to assert against. Filling the gap before any live
execution exists is intentional: if we wait, the temptation to skip the
check grows once a strategy looks profitable in paper.

One row per (strategy_id, asset_class, version). Status flow:
    pending → granted → (revoked OR expired)
`validity_until` forces re-certification — paper performance drifts, and a
six-months-old grant should not silently authorize live capital today.

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-25
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS paper_trade_certificate (
          id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          strategy_id       varchar(64)  NOT NULL,
          asset_class       varchar(16)  NOT NULL DEFAULT 'crypto',
          version           integer      NOT NULL,
          status            varchar(16)  NOT NULL DEFAULT 'pending',

          -- Evidence snapshot captured at grant time. Required to audit
          -- *why* a cert was issued, even after the underlying outcomes
          -- get pruned or the strategy mutates further.
          n_outcomes        integer      NOT NULL DEFAULT 0,
          observation_days  integer      NOT NULL DEFAULT 0,
          win_rate          numeric(8,6),
          avg_pnl_usd       numeric(18,6),
          total_pnl_usd     numeric(18,6),
          max_drawdown_pct  numeric(8,6),
          sharpe_ratio      numeric(8,4),

          -- Lifecycle
          granted_at        timestamptz,
          granted_by        varchar(64),
          validity_until    timestamptz,
          revoked_at        timestamptz,
          revoked_reason    text,

          created_at        timestamptz NOT NULL DEFAULT NOW(),
          updated_at        timestamptz NOT NULL DEFAULT NOW(),

          CONSTRAINT uq_paper_trade_cert
            UNIQUE (strategy_id, asset_class, version),
          CONSTRAINT ck_paper_trade_cert_status
            CHECK (status IN ('pending', 'granted', 'revoked', 'expired'))
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_paper_trade_cert_status "
        "ON paper_trade_certificate(status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_paper_trade_cert_strategy "
        "ON paper_trade_certificate(strategy_id, asset_class, status)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS paper_trade_certificate")

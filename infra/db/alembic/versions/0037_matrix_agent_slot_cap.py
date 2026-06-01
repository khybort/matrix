"""Cap matrix_agent slots + rebalance strategy slot pool for multi-strategy analysis.

Revision ID: 0037
Revises: 0036
Create Date: 2026-06-01

matrix_agent had no strategy_slot_configs row → unlimited per-strategy opens
while deterministic strategies were capped. With wallet at 47/50 (94%),
other strategies were starved at the paper-trade gate.

Allocations (crypto default wallet, max_concurrent_positions=80):
  matrix_agent      18  (~22% — was uncapped, dominated 34/47 opens)
  momentum_xs        8
  funding_reversion  6
  grid               6
  oi_delta           6
  screener_follow    6
  cash_and_carry     6
  dca                4
  oi_breakout        4
  (sum caps 64 — remaining 16 wallet slots as shared headroom)
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CRYPTO_WALLET_ID = "00000000-0000-0000-0000-00000000d0e1"

_SLOT_ROWS: list[tuple[str, int]] = [
    ("matrix_agent", 18),
    ("momentum_xs", 8),
    ("funding_reversion", 6),
    ("grid", 6),
    ("oi_delta", 6),
    ("screener_follow", 6),
    ("cash_and_carry", 6),
    ("dca", 4),
    ("oi_breakout", 4),
]


def upgrade() -> None:
    for strategy_id, slots in _SLOT_ROWS:
        op.execute(f"""
            INSERT INTO strategy_slot_configs
                (strategy_id, asset_class, wallet_id, allocated_slots, perf_score, consecutive_losses)
            VALUES ('{strategy_id}', 'crypto', '{_CRYPTO_WALLET_ID}'::uuid, {slots}, 0.5, 0)
            ON CONFLICT (strategy_id, asset_class, wallet_id) DO UPDATE
            SET allocated_slots = EXCLUDED.allocated_slots,
                updated_at = NOW()
        """)

    # Lower exploration while rebalancing multi-strategy paper analysis.
    op.execute("""
        UPDATE strategy_configs
        SET params = jsonb_set(
            params::jsonb,
            '{explore_epsilon}',
            '0.03'::jsonb,
            true
        )::json
        WHERE strategy_id = 'matrix_agent'
          AND asset_class = 'crypto'
          AND status = 'active'
    """)


def downgrade() -> None:
    op.execute(f"""
        DELETE FROM strategy_slot_configs
        WHERE strategy_id = 'matrix_agent'
          AND wallet_id = '{_CRYPTO_WALLET_ID}'::uuid
    """)
    op.execute("""
        UPDATE strategy_configs
        SET params = jsonb_set(
            params::jsonb,
            '{explore_epsilon}',
            '0.05'::jsonb,
            true
        )::json
        WHERE strategy_id = 'matrix_agent'
          AND asset_class = 'crypto'
          AND status = 'active'
    """)

"""seed a default BIST wallet

Phase 1 (PnL fix): paper_trade now resolves a wallet per asset_class so
each market has its own capital pool + concurrent-position slots. Crypto
already has its seed wallet (0004); BIST had none, so BIST predictions
could never open a position against a BIST wallet. Seed one with the
BIST risk caps from risk_caps.yaml (fewer slots, wider DD — T+2 settlement,
8h session).

Idempotent: ON CONFLICT (name, asset_class) DO NOTHING.

Revision ID: 0019
Revises: 0018
Create Date: 2026-05-27
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO wallets (
            id, name, asset_class, starting_capital_usd, cash_usd, locked_usd,
            max_position_pct, max_concurrent_positions, daily_loss_circuit_pct,
            equity_trailing_stop_pct, day_start_equity, day_start_at,
            created_at, updated_at
        ) VALUES (
            gen_random_uuid(), 'default', 'bist',
            10000, 10000, 0,
            0.05, 3, 0.08,
            0.15, 10000, now(),
            now(), now()
        )
        ON CONFLICT (name, asset_class) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM wallets WHERE name='default' AND asset_class='bist'")

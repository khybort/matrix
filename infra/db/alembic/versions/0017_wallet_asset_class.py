"""wallets: per-market split via asset_class column

Adds `asset_class VARCHAR(16) NOT NULL DEFAULT 'crypto'` to `wallets` so
BIST and crypto can track equity / risk caps independently inside the
same row family. Existing rows backfill to 'crypto' (matches the
historical reality — live execution has only ever happened on crypto).

The `name UNIQUE` constraint is widened to `(name, asset_class)`: two
wallets may share a name as long as their markets differ ("default" on
crypto + "default" on BIST, etc.). The UUID `id` remains the PK; the
paper-trade engine continues to address wallets by id.

Revision ID: 0017
Revises: 0016
Create Date: 2026-05-26
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Idempotent add (matches the project's convention for wallet columns —
    # see 0012_wallet_equity_trailing_stop).
    op.execute(
        "ALTER TABLE wallets ADD COLUMN IF NOT EXISTS "
        "asset_class VARCHAR(16) NOT NULL DEFAULT 'crypto'"
    )
    # Drop the legacy single-column unique on name (created in 0004).
    op.execute("ALTER TABLE wallets DROP CONSTRAINT IF EXISTS wallets_name_key")
    # Composite unique: same name allowed across markets, never within one.
    op.execute(
        "ALTER TABLE wallets ADD CONSTRAINT wallets_name_asset_class_key "
        "UNIQUE (name, asset_class)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_wallets_asset_class ON wallets(asset_class)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_wallets_asset_class")
    op.execute(
        "ALTER TABLE wallets DROP CONSTRAINT IF EXISTS wallets_name_asset_class_key"
    )
    op.execute(
        "ALTER TABLE wallets ADD CONSTRAINT wallets_name_key UNIQUE (name)"
    )
    op.execute("ALTER TABLE wallets DROP COLUMN IF EXISTS asset_class")

"""wallets: equity_trailing_stop_pct (peak-to-current drawdown circuit)

The existing daily-loss circuit trips on `(day_start_equity - equity_now) /
day_start_equity`. That misses the textbook failure mode where equity rallies
mid-session and then bleeds back below the start — at no point does the
day-start comparison flag it, even though we just gave back a 12% gain. A
trailing stop measured against the *peak* equity observed since day_start_at
catches that case.

Semantics: 0.0000 means disabled (default — preserves today's behavior for
existing wallets). e.g. 0.10 trips the circuit when current equity drops 10%
from the highest equity observed during the current day_start_at window. The
peak is derived at check time from MAX(equity_usd) on wallet_snapshots since
day_start_at, with day_start_equity as the floor — so a brand-new day with
no snapshots yet doesn't trip on a tiny intraday wobble.

Revision ID: 0012
Revises: 0011
Create Date: 2026-05-26
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # IF NOT EXISTS so test conftests that seed the column directly stay
    # compatible with `alembic upgrade head` on the same DB.
    op.execute(
        "ALTER TABLE wallets ADD COLUMN IF NOT EXISTS "
        "equity_trailing_stop_pct NUMERIC(6,4) NOT NULL DEFAULT 0.0000"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE wallets DROP COLUMN equity_trailing_stop_pct")

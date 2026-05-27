"""raise max_concurrent_positions default 5 -> 15 for faster paper learning

The agent was opening few positions partly because wallets capped concurrent
open positions at 5. During paper-trade validation we want MORE simultaneous
positions so the learning loop (outcomes -> agent_lessons) gets more samples.
Bumps the column default and lifts existing wallets still at the old default
of 5 (leaves any wallet the operator tuned to a non-5 value untouched).

Live-execution safety is unaffected: this only changes how many *paper*
positions can be open; the certificate gate (trading_safety) is independent.

Revision ID: 0021
Revises: 0020
Create Date: 2026-05-27
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE wallets ALTER COLUMN max_concurrent_positions SET DEFAULT 15")
    op.execute("UPDATE wallets SET max_concurrent_positions = 15 WHERE max_concurrent_positions = 5")


def downgrade() -> None:
    op.execute("ALTER TABLE wallets ALTER COLUMN max_concurrent_positions SET DEFAULT 5")
    op.execute("UPDATE wallets SET max_concurrent_positions = 5 WHERE max_concurrent_positions = 15")

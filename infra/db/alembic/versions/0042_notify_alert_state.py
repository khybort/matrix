"""Durable notify alert state.

Revision ID: 0042
Revises: 0041
Create Date: 2026-10-09

notify kept the shadow tracker's last verdict per strategy, and whether
`review_due` had been sent, in /tmp inside its container: a recreate (image
rebuild, compose up) wiped it, so a persisting `broken` re-fired and a
`review_due` that never reached Telegram was marked sent anyway. One row per
alert family (`key`), the state as jsonb. Local tier: each node's notify
delivers its own alerts.
"""

from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE IF NOT EXISTS notify_alert_state ("
        " key text PRIMARY KEY,"
        " state jsonb NOT NULL,"
        " updated_at timestamptz NOT NULL DEFAULT now())"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS notify_alert_state")

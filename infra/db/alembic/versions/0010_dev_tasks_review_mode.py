"""dev_tasks: per-task review_mode (auto vs manual)

Successful tasks land in `awaiting_review` by default today, and a human has
to POST /accept or /discard to finalize. Operators wanted the common path
(small, well-scoped tasks) to skip that gate. `review_mode='auto'` flips
the worker to mark the task `merged` directly with reviewed_by='auto';
`manual` keeps today's awaiting_review behavior. Failed tasks ignore the
column — failure stays its own terminal state.

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-25
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE dev_tasks
        ADD COLUMN review_mode TEXT NOT NULL DEFAULT 'auto'
            CHECK (review_mode IN ('auto', 'manual'))
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE dev_tasks DROP COLUMN review_mode")

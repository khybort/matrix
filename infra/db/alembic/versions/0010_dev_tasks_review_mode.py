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
    # `IF NOT EXISTS` because tests/conftest.py seeds the column on the LOCAL
    # tier directly so the unit suite can run without applying alembic; that
    # leaves the column present but alembic_version at 0009. The IF NOT EXISTS
    # also makes the check constraint conditional via DO block.
    op.execute(
        "ALTER TABLE dev_tasks ADD COLUMN IF NOT EXISTS review_mode "
        "TEXT NOT NULL DEFAULT 'auto'"
    )
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'dev_tasks_review_mode_check'
            ) THEN
                ALTER TABLE dev_tasks
                ADD CONSTRAINT dev_tasks_review_mode_check
                CHECK (review_mode IN ('auto', 'manual'));
            END IF;
        END
        $$
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE dev_tasks DROP COLUMN review_mode")

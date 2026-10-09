"""Record public margin borrow rates, every coin, every venue the carry uses.

Revision ID: 0041
Revises: 0040
Create Date: 2026-10-09

`neg_funding_carry` shorts spot on borrowed coins, and its edge stands or falls
on what that borrow costs during a squeeze. Neither venue publishes historical
borrow rates, so the paper book charged the entry quote x a guessed stress
multiple and could not falsify it. `ingestion.borrow_recorder` now polls the
same public tables the strategy reads (Bybit spot-margin VIP0, Binance
cross-margin VIP0) and writes here; the paper engine charges a carry's borrow
from this series (backtest.carry_books).

One row per (venue, coin) whenever the rate or limit changed, and at least
hourly otherwise, so a reader can tell "unchanged" from "recorder down".
`max_borrow` is the venue's published per-account limit in coin units (Bybit
`maxBorrowingAmount`, Binance VIP0 `borrowLimit`), not pool availability:
no public endpoint exposes pool size or utilisation. Local tier, 180 days
kept (pruned by the recorder through the `ts` index).
"""

from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE IF NOT EXISTS margin_borrow_rates ("
        " venue text NOT NULL,"
        " coin text NOT NULL,"
        " ts timestamptz NOT NULL,"
        " hourly_rate numeric NOT NULL,"
        " max_borrow numeric,"
        " borrowable boolean NOT NULL DEFAULT true,"
        " PRIMARY KEY (venue, coin, ts))"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_margin_borrow_rates_ts ON margin_borrow_rates (ts)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS margin_borrow_rates")

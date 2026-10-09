"""Daily Deribit option-IV term features for the r5f forward test.

Revision ID: 0043
Revises: 0042
Create Date: 2026-10-09

Round 5's near miss D.3 (front-end IV inversion -> long BTC / ETH perp for
3 days) is being forward-tested (r5f, n = 60,
services/backtest/research/signal_2026_10_r5f/PREREG.md). Its input is a
percentile of TERM = front ATM IV - back ATM IV, computed from the Deribit
option trades in [D-4h, D) of each 00:00 UTC decision day D, over the 365 days
before D. `ingestion.iv_term_recorder` writes one row per (currency, day) with
the inputs that made TERM (matrix_shared.iv_term.term_features); the shadow
module `iv_inversion` and the r5f evaluation read it. `term` NULL = a bucket had
fewer than 5 trades (missing by the rule, not a failure). Local tier.
"""

from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE IF NOT EXISTS deribit_iv_daily ("
        " currency text NOT NULL,"
        " day date NOT NULL,"
        " n_trades integer NOT NULL,"
        " n_front integer NOT NULL,"
        " n_back integer NOT NULL,"
        " front_iv double precision,"
        " back_iv double precision,"
        " term double precision,"
        " put_notional double precision NOT NULL,"
        " call_notional double precision NOT NULL,"
        " recorded_at timestamptz NOT NULL DEFAULT now(),"
        " PRIMARY KEY (currency, day))"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS deribit_iv_daily")

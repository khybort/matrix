"""Bybit liquidations, every USDT perp, for the r2f forward test.

Revision ID: 0044
Revises: 0043
Create Date: 2026-10-09

Round 2's H8b cascade fade had to use an aggressor-burst proxy because no
venue serves historical liquidations (Binance liquidationSnapshot: 404). From
now on they are recorded: `ingestion.liquidation_recorder` subscribes Bybit's
public `allLiquidation.<symbol>` for every trading USDT perp and writes each
event here. `side` is the position liquidated (Bybit "Buy" = long), `price`
the bankruptcy price, `notional_usd` = size x price.

`bybit_liquidation_minutes` has one row per UTC minute during which every
socket was connected, subscribed and receiving frames, written after the
minute's events were flushed. "No liquidation" is only evidence of quiet when
its minute is here; the r2f rule (matrix_shared.liq_cascade) requires it, and
the shadow module stands down when the newest row is old.

Local tier, 180 days kept (pruned hourly by the recorder through the ts /
minute indexes).
"""

from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE IF NOT EXISTS bybit_liquidations ("
        " symbol text NOT NULL,"
        " ts timestamptz NOT NULL,"
        " side text NOT NULL,"
        " size numeric NOT NULL,"
        " price numeric NOT NULL,"
        " notional_usd double precision NOT NULL)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_bybit_liquidations_symbol_ts ON bybit_liquidations (symbol, ts)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_bybit_liquidations_ts ON bybit_liquidations (ts)")
    op.execute(
        "CREATE TABLE IF NOT EXISTS bybit_liquidation_minutes ("
        " minute timestamptz PRIMARY KEY,"
        " n_symbols integer NOT NULL,"
        " n_events integer NOT NULL)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS bybit_liquidation_minutes")
    op.execute("DROP TABLE IF EXISTS bybit_liquidations")

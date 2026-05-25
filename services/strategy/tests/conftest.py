"""Shared fixtures for strategy module tests.

trade_flow_imbalance reads MarketTrade from the LOCAL tier. Tests seed
a deterministic burst of trades for a synthetic symbol (TEST_SYM) so we
don't collide with the live BTCUSDT/ETHUSDT data; the symbol filter in
the module restricts evaluation per its `symbols` arg.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest_asyncio
from sqlalchemy import delete

from matrix_shared import local_session_scope
from matrix_shared.models import MarketTrade

TEST_LOCAL_DSN = os.environ.get(
    "STRATEGY_TEST_LOCAL_DSN",
    "postgres://matrix:matrix_dev_only@localhost:5432/matrix",
)
os.environ.setdefault("LOCAL_DATABASE_URL", TEST_LOCAL_DSN)
os.environ.setdefault("SHARED_DATABASE_URL", TEST_LOCAL_DSN)

TEST_SYM = "TESTUSDT"


@pytest_asyncio.fixture
async def seed_trades():
    """Insert a deterministic series of MarketTrade rows for TEST_SYM and
    clean them up after. Each call appends; the symbol is unique to tests
    so no risk of polluting live data. Pre-purges any leftover TEST_SYM
    rows defensively — fixture cleanup races have been observed at the
    edge of the LOOKBACK_S window."""
    async with local_session_scope() as session:
        await session.execute(delete(MarketTrade).where(MarketTrade.symbol == TEST_SYM))

    inserted: list[str] = []

    async def _seed(
        buy_count: int = 80,
        sell_count: int = 20,
        size_per_trade: Decimal = Decimal("1"),
        price: Decimal = Decimal("100"),
    ) -> None:
        now = datetime.now(timezone.utc)
        async with local_session_scope() as session:
            for i in range(buy_count + sell_count):
                tid = f"strat-test-{uuid.uuid4().hex[:8]}-{i}"
                session.add(
                    MarketTrade(
                        id=uuid.uuid4(),
                        exchange="bybit",
                        exchange_trade_id=tid,
                        symbol=TEST_SYM,
                        trade_ts=now - timedelta(seconds=60 - i),
                        side="buy" if i < buy_count else "sell",
                        price=price,
                        size=size_per_trade,
                    )
                )
                inserted.append(tid)

    yield _seed

    if inserted:
        async with local_session_scope() as session:
            await session.execute(
                delete(MarketTrade).where(MarketTrade.exchange_trade_id.in_(inserted))
            )

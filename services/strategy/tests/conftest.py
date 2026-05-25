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

from matrix_shared import local_session_scope, shared_session_scope
from matrix_shared.models import MarketBar, MarketTrade, Prediction

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


@pytest_asyncio.fixture
async def seed_bars():
    """Insert 1m crypto MarketBar rows for TEST_SYM around a configurable
    price corridor. Cleans up TEST_SYM bars on teardown."""
    async with local_session_scope() as session:
        await session.execute(delete(MarketBar).where(MarketBar.symbol == TEST_SYM))

    async def _seed(
        *,
        low: Decimal = Decimal("100"),
        high: Decimal = Decimal("200"),
        hours: int = 24,
        last_close: Decimal | None = None,
        prior_close: Decimal | None = None,
    ) -> None:
        """Seed `hours*60` 1m bars. The earliest bar's close is `low`, the
        latest minus-one is `high` (so MIN/MAX span [low,high]). The last
        two bars are overridden by `prior_close` and `last_close` when
        provided, which lets tests stage a specific cross.
        """
        now = datetime.now(timezone.utc)
        n = hours * 60
        async with local_session_scope() as session:
            for i in range(n):
                ts = now - timedelta(minutes=(n - 1 - i))
                if i == 0:
                    close = low
                elif i == n - 2 and prior_close is not None:
                    close = prior_close
                elif i == n - 1 and last_close is not None:
                    close = last_close
                elif i == n // 2:
                    close = high
                else:
                    # linear ramp from low to high over the first half, back down
                    if i < n // 2:
                        frac = Decimal(i) / Decimal(n // 2)
                        close = low + (high - low) * frac
                    else:
                        frac = Decimal(i - n // 2) / Decimal(n - n // 2)
                        close = high - (high - low) * frac
                session.add(
                    MarketBar(
                        id=uuid.uuid4(),
                        symbol=TEST_SYM,
                        asset_class="crypto",
                        interval="1m",
                        ts=ts,
                        open=close,
                        high=close,
                        low=close,
                        close=close,
                        volume=Decimal("1"),
                        source="test",
                        created_at=now,
                    )
                )

    yield _seed

    async with local_session_scope() as session:
        await session.execute(delete(MarketBar).where(MarketBar.symbol == TEST_SYM))


@pytest_asyncio.fixture
async def seed_predictions():
    """Insert Prediction rows on the SHARED tier (which is the same DB as
    LOCAL in tests). Returns a callable that takes strategy_id, side, and
    minutes_ago. Cleans up TEST_SYM predictions on teardown."""
    async with shared_session_scope() as session:
        await session.execute(delete(Prediction).where(Prediction.symbol == TEST_SYM))

    async def _seed(
        *,
        strategy_id: str,
        side: str = "long",
        minutes_ago: int = 5,
        price: Decimal = Decimal("100"),
    ) -> uuid.UUID:
        now = datetime.now(timezone.utc)
        gen_at = now - timedelta(minutes=minutes_ago)
        pid = uuid.uuid4()
        async with shared_session_scope() as session:
            session.add(
                Prediction(
                    id=pid,
                    strategy_id=strategy_id,
                    strategy_version=1,
                    generated_at=gen_at,
                    symbol=TEST_SYM,
                    exchange="bybit",
                    asset_class="crypto",
                    side=side,
                    confidence=Decimal("0.3"),
                    horizon_seconds=300,
                    close_by=gen_at + timedelta(seconds=300),
                    entry_price_ref=price,
                    thesis="seed",
                    context={},
                    status="open",
                )
            )
        return pid

    yield _seed

    async with shared_session_scope() as session:
        await session.execute(delete(Prediction).where(Prediction.symbol == TEST_SYM))

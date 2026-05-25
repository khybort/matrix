"""Shared fixtures for backtest tests.

Two flavors of test live here:

* paper_trade live-DB tests use UUID-scoped wallet/prediction/trade fixtures
  against matrix-postgres + matrix-postgres-shared. Production data untouched.
* historical engine tests use pure-Python synthetic MarketBar lists — no DB.

Backtest's `_latest_price` reads LOCAL `market_trades`; we seed a tick per
position so `close_due_positions` has something to mark out at.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import delete

from matrix_shared import local_session_scope, shared_session_scope
from matrix_shared.models import (
    MarketBar,
    MarketTrade,
    PaperPosition,
    Prediction,
    Wallet,
    WalletSnapshot,
)

# Match dev_agent's localhost approach; both DBs are exposed via docker.
TEST_LOCAL_DSN = os.environ.get(
    "BACKTEST_TEST_LOCAL_DSN",
    "postgres://matrix:matrix_dev_only@localhost:5432/matrix",
)
TEST_SHARED_DSN = os.environ.get(
    "BACKTEST_TEST_SHARED_DSN",
    "postgres://matrix:matrix_dev_only@localhost:5433/matrix_shared",
)
os.environ.setdefault("LOCAL_DATABASE_URL", TEST_LOCAL_DSN)
os.environ.setdefault("SHARED_DATABASE_URL", TEST_SHARED_DSN)


@pytest_asyncio.fixture
async def wallet_id() -> uuid.UUID:
    wid = uuid.uuid4()
    async with shared_session_scope() as session:
        session.add(
            Wallet(
                id=wid,
                name=f"test-bt-{wid}",
                starting_capital_usd=Decimal("10000"),
                cash_usd=Decimal("10000"),
                locked_usd=Decimal("0"),
                max_position_pct=Decimal("0.02"),
                max_concurrent_positions=5,
                daily_loss_circuit_pct=Decimal("0.05"),
                day_start_equity=Decimal("10000"),
                day_start_at=datetime.now(timezone.utc),
            )
        )
    yield wid
    async with shared_session_scope() as session:
        await session.execute(delete(PaperPosition).where(PaperPosition.wallet_id == wid))
        await session.execute(delete(WalletSnapshot).where(WalletSnapshot.wallet_id == wid))
        await session.execute(delete(Wallet).where(Wallet.id == wid))


@pytest_asyncio.fixture
async def make_prediction():
    """Factory returning a Prediction id. Predictions are auto-cleaned by
    deleting after each test; PaperPosition rows on them are deleted first
    via the wallet_id fixture or test-local code."""
    created: list[uuid.UUID] = []

    async def _make(
        *,
        strategy_id: str = "test_strat",
        version: int = 1,
        asset_class: str = "crypto",
        symbol: str = "BTCUSDT",
        side: str = "long",
        confidence: Decimal = Decimal("0.5"),
        horizon_seconds: int = 60,
        generated_at: datetime | None = None,
        close_by_offset: timedelta = timedelta(seconds=60),
        status: str = "open",
    ) -> uuid.UUID:
        pid = uuid.uuid4()
        gen = generated_at or datetime.now(timezone.utc)
        async with shared_session_scope() as session:
            session.add(
                Prediction(
                    id=pid,
                    strategy_id=strategy_id,
                    strategy_version=version,
                    asset_class=asset_class,
                    symbol=symbol,
                    exchange="bybit",
                    side=side,
                    confidence=confidence,
                    horizon_seconds=horizon_seconds,
                    generated_at=gen,
                    close_by=gen + close_by_offset,
                    entry_price_ref=Decimal("100"),
                    status=status,
                )
            )
        created.append(pid)
        return pid

    yield _make

    if created:
        async with shared_session_scope() as session:
            await session.execute(delete(Prediction).where(Prediction.id.in_(created)))


@pytest_asyncio.fixture
async def seed_recent_trades():
    """Drop a fresh market trade per (symbol, exchange) so `_latest_price`
    has something to mark out at. Trades are tagged with a session-unique
    exchange-id prefix and cleaned up after."""
    tag = f"bt-test-{uuid.uuid4().hex[:8]}"
    inserted: list[str] = []

    async def _seed(symbol: str = "BTCUSDT", price: Decimal = Decimal("100")) -> None:
        trade_id = f"{tag}-{symbol}-{datetime.now(timezone.utc).timestamp()}"
        async with local_session_scope() as session:
            session.add(
                MarketTrade(
                    id=uuid.uuid4(),
                    exchange="bybit",
                    exchange_trade_id=trade_id,
                    symbol=symbol,
                    trade_ts=datetime.now(timezone.utc),
                    side="buy",
                    price=price,
                    size=Decimal("1"),
                )
            )
        inserted.append(trade_id)

    yield _seed

    if inserted:
        async with local_session_scope() as session:
            await session.execute(
                delete(MarketTrade).where(MarketTrade.exchange_trade_id.in_(inserted))
            )


# --- historical-engine fixtures (pure Python, no DB) ---------------------

def make_bar(
    *,
    ts: datetime,
    close: Decimal,
    symbol: str = "BTCUSDT",
    asset_class: str = "crypto",
    interval: str = "1m",
    open_: Decimal | None = None,
    high: Decimal | None = None,
    low: Decimal | None = None,
    volume: Decimal = Decimal("1"),
) -> MarketBar:
    """Construct a detached MarketBar (no session). Defaults OHLC = close."""
    return MarketBar(
        id=uuid.uuid4(),
        symbol=symbol,
        asset_class=asset_class,
        interval=interval,
        ts=ts,
        open=open_ if open_ is not None else close,
        high=high if high is not None else close,
        low=low if low is not None else close,
        close=close,
        volume=volume,
        source="synthetic",
        created_at=ts,
    )


@pytest.fixture
def flat_bars() -> list[MarketBar]:
    """1500 bars at a constant price → no grid crossover should fire."""
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        make_bar(ts=base + timedelta(minutes=i), close=Decimal("50000"))
        for i in range(1500)
    ]


@pytest.fixture
def swinging_bars() -> list[MarketBar]:
    """1500 warm-up bars at 50000 + 200 more bars sweeping through a wide
    band (49000 → 51000 and back). Guarantees the rolling-window mid
    settles near 50000 and the sweep crosses grid lines."""
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    bars = [
        make_bar(ts=base + timedelta(minutes=i), close=Decimal("50000"))
        for i in range(1500)
    ]
    for i in range(100):
        px = Decimal("50000") - Decimal(i * 10)  # 50000 → 49010
        bars.append(make_bar(ts=base + timedelta(minutes=1500 + i), close=px))
    for i in range(100):
        px = Decimal("49010") + Decimal(i * 10)  # 49010 → 50000
        bars.append(make_bar(ts=base + timedelta(minutes=1600 + i), close=px))
    return bars


@pytest.fixture
def dip_then_uptrend_bars() -> list[MarketBar]:
    """Warm-up flat at 50000, then ONE sharp dip-bar that crosses several
    grid lines simultaneously, then immediate recovery well above entry.
    horizon_s=300 → 5 bars, so recovery must arrive inside that window."""
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    bars = [
        make_bar(ts=base + timedelta(minutes=i), close=Decimal("50000"))
        for i in range(1500)
    ]
    bars.append(make_bar(ts=base + timedelta(minutes=1500), close=Decimal("49500")))
    for i in range(20):
        bars.append(
            make_bar(ts=base + timedelta(minutes=1501 + i), close=Decimal("50500"))
        )
    return bars

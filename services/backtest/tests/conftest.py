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

import pytest_asyncio
from sqlalchemy import delete

from matrix_shared import local_session_scope, shared_session_scope
from matrix_shared.models import (
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
        symbol: str = "TEST_BTCUSDT",
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


# Tests must NEVER seed market_trades for live symbols (BTCUSDT/ETHUSDT). The
# live paper engine queries `_latest_price` per tick — if a test's $100-priced
# row lands between the live engine reading and a real Bybit tick arriving,
# the engine closes a real position at a fake price. We hit this once: a
# BTCUSDT test row caused a $102,516 spurious "win" that took the wallet
# from $9.5k to $112k before being reversed manually. Lesson encoded here.
_TEST_SYMBOL_PREFIX = "TEST_"
_LIVE_SYMBOLS = frozenset({"BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"})


@pytest_asyncio.fixture
async def seed_recent_trades():
    """Drop a fresh market trade so `_latest_price` has something to mark out
    at. Tagged with a session-unique exchange-id prefix and cleaned up after.

    REFUSES live symbols — see the comment above for why. Tests should pass
    a TEST_xxx symbol; the paper_trade code under test doesn't care about
    symbol semantics, only that prediction.symbol matches the seeded row."""
    tag = f"bt-test-{uuid.uuid4().hex[:8]}"
    inserted: list[str] = []

    async def _seed(
        symbol: str = "TEST_BTCUSDT", price: Decimal = Decimal("100")
    ) -> None:
        if symbol in _LIVE_SYMBOLS:
            raise ValueError(
                f"seed_recent_trades refuses live symbol {symbol!r}; use "
                f"a TEST_ prefixed symbol so the live paper engine ignores it."
            )
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


# Historical-engine synthetic-bar fixtures live in tests/historical/conftest.py
# to keep them isolated from the live-DB fixtures here. Mixing pytest-asyncio's
# session loop with the sync historical tests gets the cached engine into a
# "different loop" state — splitting the test trees avoids it cleanly.

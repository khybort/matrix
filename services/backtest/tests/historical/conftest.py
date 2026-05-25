"""Synthetic-bar fixtures for the historical backtest engine.

Isolated from the live-DB conftest one level up: those fixtures cache an
async sqlalchemy engine on the session loop, and pytest-asyncio's
loop-resume around sync tests gets the engine confused. Keeping these
tests in their own subdir with no DB imports keeps both worlds happy.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from matrix_shared.models import MarketBar


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
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        make_bar(ts=base + timedelta(minutes=i), close=Decimal("50000"))
        for i in range(1500)
    ]


@pytest.fixture
def swinging_bars() -> list[MarketBar]:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    bars = [
        make_bar(ts=base + timedelta(minutes=i), close=Decimal("50000"))
        for i in range(1500)
    ]
    for i in range(100):
        px = Decimal("50000") - Decimal(i * 10)
        bars.append(make_bar(ts=base + timedelta(minutes=1500 + i), close=px))
    for i in range(100):
        px = Decimal("49010") + Decimal(i * 10)
        bars.append(make_bar(ts=base + timedelta(minutes=1600 + i), close=px))
    return bars


@pytest.fixture
def dip_then_uptrend_bars() -> list[MarketBar]:
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

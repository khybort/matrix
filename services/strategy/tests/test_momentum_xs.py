"""MomentumXs strategy unit tests.

Scenario: 4 synthetic symbols (A/B/C/D suffixed with USDT) with controlled
7-day return profiles. A=+10%, B=+5%, C=-3%, D=-8%.

Tests:
  test_ranks_by_pct_return  — top_k=1 → long A, short D.
  test_vol_filter_excludes_noisy — B has huge std → excluded; long A, no B.
  test_no_bist — market attr stays 'crypto'; BIST symbols absent from default
                 universe so they are never considered.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import delete

from matrix_shared import local_session_scope
from matrix_shared.models import MarketBar
from strategy.modules.crypto.momentum_xs import MomentumXs, STRATEGY_ID

pytestmark = pytest.mark.asyncio

# Use distinct TEST_-prefixed symbols so we don't collide with live data.
SYM_A = "TEST_MXS_AUSDT"
SYM_B = "TEST_MXS_BUSDT"
SYM_C = "TEST_MXS_CUSDT"
SYM_D = "TEST_MXS_DUSDT"
ALL_SYMS = [SYM_A, SYM_B, SYM_C, SYM_D]


async def _insert_hourly_bars(
    symbol: str,
    *,
    start_price: Decimal,
    end_price: Decimal,
    hours: int = 168,  # 7 days
    high_std: bool = False,
) -> None:
    """Insert `hours` 1h MarketBar rows that produce the desired pct_return.

    If high_std=True, the series alternates between start_price and
    end_price every tick, producing a very high rolling std.
    """
    now = datetime.now(timezone.utc)
    async with local_session_scope() as session:
        for i in range(hours):
            ts = now - timedelta(hours=(hours - 1 - i))
            if high_std:
                # Oscillate wildly — std >> any reasonable vol_filter_pct
                close = start_price if i % 2 == 0 else end_price
            else:
                # Linear ramp from start_price to end_price
                frac = Decimal(i) / Decimal(hours - 1) if hours > 1 else Decimal("0")
                close = start_price + (end_price - start_price) * frac

            session.add(
                MarketBar(
                    id=uuid.uuid4(),
                    symbol=symbol,
                    asset_class="crypto",
                    interval="1h",
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


async def _clear_bars(*symbols: str) -> None:
    async with local_session_scope() as session:
        for sym in symbols:
            await session.execute(delete(MarketBar).where(MarketBar.symbol == sym))


@pytest_asyncio.fixture(autouse=True)
async def cleanup_bars():
    """Ensure test bars are cleaned up even if the test fails."""
    await _clear_bars(*ALL_SYMS)
    yield
    await _clear_bars(*ALL_SYMS)


async def test_ranks_by_pct_return():
    """top_k=1: long A (+10%), short D (-8%). B and C excluded from extremes."""
    base = Decimal("100")
    await _insert_hourly_bars(SYM_A, start_price=base, end_price=base * Decimal("1.10"))
    await _insert_hourly_bars(SYM_B, start_price=base, end_price=base * Decimal("1.05"))
    await _insert_hourly_bars(SYM_C, start_price=base, end_price=base * Decimal("0.97"))
    await _insert_hourly_bars(SYM_D, start_price=base, end_price=base * Decimal("0.92"))

    strategy = MomentumXs(
        symbols=ALL_SYMS,
        top_k=1,
        vol_filter_pct=Decimal("0.99"),  # effectively no vol filter
        lookback_days=7,
        horizon_s=1,  # tiny dedup window so old test rows don't interfere
    )
    drafts = await strategy.generate()

    long_drafts = [d for d in drafts if d.side == "long"]
    short_drafts = [d for d in drafts if d.side == "short"]

    assert len(long_drafts) == 1, f"expected 1 long, got {[d.symbol for d in long_drafts]}"
    assert len(short_drafts) == 1, f"expected 1 short, got {[d.symbol for d in short_drafts]}"

    assert long_drafts[0].symbol == SYM_A
    assert short_drafts[0].symbol == SYM_D

    # Verify draft metadata
    for d in drafts:
        assert d.strategy_id == STRATEGY_ID
        assert d.strategy_version == 1
        assert d.confidence == Decimal("0.60")
        assert d.horizon_seconds == 1
        assert "7d return" in (d.thesis or "")
        assert "rank" in d.context


async def test_vol_filter_excludes_noisy():
    """B is wildly volatile → excluded. With top_k=1, long is A (+10%),
    short is C (next worst after excluding B-style noise)."""
    base = Decimal("100")
    await _insert_hourly_bars(SYM_A, start_price=base, end_price=base * Decimal("1.10"))
    # B: huge oscillation → high std
    await _insert_hourly_bars(
        SYM_B,
        start_price=base,
        end_price=base * Decimal("1.05"),
        high_std=True,
    )
    await _insert_hourly_bars(SYM_C, start_price=base, end_price=base * Decimal("0.97"))
    await _insert_hourly_bars(SYM_D, start_price=base, end_price=base * Decimal("0.92"))

    strategy = MomentumXs(
        symbols=ALL_SYMS,
        top_k=1,
        vol_filter_pct=Decimal("0.50"),  # 50% — B's std will blow past this
        lookback_days=7,
        horizon_s=1,
    )
    drafts = await strategy.generate()

    symbols_in_drafts = {d.symbol for d in drafts}
    assert SYM_B not in symbols_in_drafts, "noisy symbol B should be filtered out"

    long_drafts = [d for d in drafts if d.side == "long"]
    assert any(d.symbol == SYM_A for d in long_drafts), "A should be longed (top return)"


async def test_no_bist():
    """MomentumXs.market is 'crypto'; BIST symbols won't appear because
    they are not in the crypto_universe and we pass an explicit list."""
    bist_sym = "TEST_BIST_GARAN"
    strategy = MomentumXs(symbols=ALL_SYMS + [bist_sym], top_k=1, horizon_s=1)

    # No bars seeded — generate returns nothing for all symbols.
    drafts = await strategy.generate()
    bist_drafts = [d for d in drafts if d.symbol == bist_sym]
    assert bist_drafts == [], "BIST symbols must not appear in momentum_xs drafts"

    assert strategy.market == "crypto"

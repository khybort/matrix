"""xexch_funding_arb strategy module tests.

Seeds funding snapshots on BOTH venues (bybit + binance) for a test symbol and
checks the differential logic:
  - wide differential → emits xexch_carry, shorts the higher-funding venue
  - narrow differential (< min_diff) → no draft
  - only one venue present → no draft (can't difference)
  - cooldown suppresses a repeat while a position is open
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import delete

from matrix_shared import local_session_scope, shared_session_scope
from matrix_shared.models import Prediction, TickerSnapshot

from strategy.modules.crypto.xexch_funding_arb import XexchFundingArb, STRATEGY_ID

from tests.conftest import TEST_SYM

pytestmark = pytest.mark.asyncio

_SYM = f"XXTEST_{TEST_SYM}"


async def _insert_ticker(symbol: str, exchange: str, funding_rate: Decimal) -> None:
    async with local_session_scope() as session:
        session.add(
            TickerSnapshot(
                id=uuid.uuid4(),
                exchange=exchange,
                symbol=symbol,
                snapshot_ts=datetime.now(timezone.utc),
                last_price=Decimal("50000"),
                mark_price=Decimal("50000"),
                funding_rate=funding_rate,
            )
        )


async def _cleanup_ticker(symbol: str) -> None:
    async with local_session_scope() as session:
        await session.execute(
            delete(TickerSnapshot).where(TickerSnapshot.symbol == symbol)
        )


async def _cleanup_predictions(symbol: str) -> None:
    async with shared_session_scope() as session:
        await session.execute(delete(Prediction).where(Prediction.symbol == symbol))


async def test_wide_differential_emits_and_shorts_high_venue():
    """bybit funding 0.06% vs binance -0.01% → Δ=0.07%/8h (> 0.05 floor).
    Bybit is higher → we SHORT bybit."""
    symbol = f"{_SYM}_WIDE"
    await _insert_ticker(symbol, "bybit", Decimal("0.0006"))
    await _insert_ticker(symbol, "binance", Decimal("-0.0001"))
    try:
        drafts = await XexchFundingArb(symbols=[symbol]).generate()
        xx = [d for d in drafts if d.symbol == symbol]
        assert len(xx) == 1, f"Expected 1 draft, got {len(xx)}"
        d = xx[0]
        assert d.side == "xexch_carry"
        assert d.strategy_id == STRATEGY_ID
        assert d.context["xexch_short_venue"] == "bybit"
        # funding_diff_8h is the positive capture magnitude = |0.0006 - (-0.0001)|
        assert Decimal(d.context["funding_diff_8h"]) == Decimal("0.0007")
        assert Decimal("0.1") <= d.confidence <= Decimal("1.0")
    finally:
        await _cleanup_ticker(symbol)


async def test_narrow_differential_no_emit():
    """Δ below min_diff → no arb."""
    symbol = f"{_SYM}_NARROW"
    await _insert_ticker(symbol, "bybit", Decimal("0.0003"))
    await _insert_ticker(symbol, "binance", Decimal("0.00025"))  # Δ=0.005%/8h
    try:
        drafts = await XexchFundingArb(symbols=[symbol]).generate()
        assert [d for d in drafts if d.symbol == symbol] == []
    finally:
        await _cleanup_ticker(symbol)


async def test_short_binance_when_binance_higher():
    """binance funding higher → we SHORT binance."""
    symbol = f"{_SYM}_BN"
    await _insert_ticker(symbol, "bybit", Decimal("-0.0002"))
    await _insert_ticker(symbol, "binance", Decimal("0.0006"))
    try:
        drafts = await XexchFundingArb(symbols=[symbol]).generate()
        xx = [d for d in drafts if d.symbol == symbol]
        assert len(xx) == 1
        assert xx[0].context["xexch_short_venue"] == "binance"
        assert Decimal(xx[0].context["funding_diff_8h"]) == Decimal("0.0008")
    finally:
        await _cleanup_ticker(symbol)


async def test_single_venue_no_emit():
    """Only one venue has a snapshot → can't difference → no draft."""
    symbol = f"{_SYM}_SOLO"
    await _insert_ticker(symbol, "bybit", Decimal("0.0009"))
    try:
        drafts = await XexchFundingArb(symbols=[symbol]).generate()
        assert [d for d in drafts if d.symbol == symbol] == []
    finally:
        await _cleanup_ticker(symbol)


async def test_cooldown_blocks_repeat():
    symbol = f"{_SYM}_COOLDOWN"
    await _insert_ticker(symbol, "bybit", Decimal("0.0006"))
    await _insert_ticker(symbol, "binance", Decimal("-0.0001"))
    try:
        drafts1 = await XexchFundingArb(symbols=[symbol]).generate()
        assert len([d for d in drafts1 if d.symbol == symbol]) == 1

        draft = drafts1[0]
        now = datetime.now(timezone.utc)
        async with shared_session_scope() as session:
            session.add(
                Prediction(
                    id=uuid.uuid4(),
                    strategy_id=draft.strategy_id,
                    strategy_version=draft.strategy_version,
                    generated_at=now,
                    symbol=symbol,
                    exchange=draft.exchange,
                    asset_class="crypto",
                    side="xexch_carry",
                    confidence=draft.confidence,
                    horizon_seconds=draft.horizon_seconds,
                    close_by=now + timedelta(seconds=draft.horizon_seconds),
                    entry_price_ref=draft.entry_price_ref,
                    thesis=draft.thesis,
                    context=draft.context,
                    status="open",
                )
            )

        drafts2 = await XexchFundingArb(symbols=[symbol]).generate()
        assert [d for d in drafts2 if d.symbol == symbol] == []
    finally:
        await _cleanup_ticker(symbol)
        await _cleanup_predictions(symbol)

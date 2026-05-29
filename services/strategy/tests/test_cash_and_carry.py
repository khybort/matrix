"""cash_and_carry strategy module tests.

Covers:
  - test_high_funding_emits_delta_neutral: funding_rate above MIN → draft emitted
  - test_low_funding_no_emit: funding_rate below MIN → no draft
  - test_cooldown_blocks_repeat: open delta_neutral prediction on same symbol →
    second call returns empty
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import delete

from matrix_shared import local_session_scope, shared_session_scope
from matrix_shared.models import Prediction, TickerSnapshot

from strategy.modules.crypto.cash_and_carry import (
    DEFAULT_MIN_FUNDING,
    CashAndCarry,
    STRATEGY_ID,
)

from tests.conftest import TEST_SYM

pytestmark = pytest.mark.asyncio

# A symbol scoped to these tests — not in the live universe.
_SYM = f"CCTEST_{TEST_SYM}"
_EXCHANGE = "bybit"


async def _insert_ticker(symbol: str, funding_rate: Decimal) -> uuid.UUID:
    """Seed a TickerSnapshot with the given funding_rate on the LOCAL tier."""
    now = datetime.now(timezone.utc)
    tid = uuid.uuid4()
    async with local_session_scope() as session:
        session.add(
            TickerSnapshot(
                id=tid,
                exchange=_EXCHANGE,
                symbol=symbol,
                snapshot_ts=now,
                last_price=Decimal("50000"),
                mark_price=Decimal("50000"),
                funding_rate=funding_rate,
            )
        )
    return tid


async def _cleanup_ticker(symbol: str) -> None:
    async with local_session_scope() as session:
        await session.execute(
            delete(TickerSnapshot).where(TickerSnapshot.symbol == symbol)
        )


async def _cleanup_predictions(symbol: str) -> None:
    async with shared_session_scope() as session:
        await session.execute(
            delete(Prediction).where(Prediction.symbol == symbol)
        )


async def test_high_funding_emits_delta_neutral():
    """A funding_rate above DEFAULT_MIN_FUNDING → generate() emits a
    delta_neutral draft for that symbol."""
    symbol = f"{_SYM}_HIGH"
    await _insert_ticker(symbol, Decimal("0.0005"))  # 0.05% / 8h — well above floor
    try:
        drafts = await CashAndCarry(symbols=[symbol]).generate()
        dn = [d for d in drafts if d.symbol == symbol]
        assert len(dn) == 1, f"Expected 1 draft, got {len(dn)}"
        d = dn[0]
        assert d.side == "delta_neutral"
        assert d.strategy_id == STRATEGY_ID
        assert d.strategy_version == 1
        assert d.horizon_seconds == 28800
        assert Decimal("0.1") <= d.confidence <= Decimal("1.0")
        assert "delta-neutral funding capture" in d.thesis
        assert "funding_rate_8h" in d.context
    finally:
        await _cleanup_ticker(symbol)


async def test_low_funding_no_emit():
    """A funding_rate below DEFAULT_MIN_FUNDING → generate() emits nothing."""
    symbol = f"{_SYM}_LOW"
    low_fr = DEFAULT_MIN_FUNDING - Decimal("0.00001")  # just below floor
    await _insert_ticker(symbol, low_fr)
    try:
        drafts = await CashAndCarry(symbols=[symbol]).generate()
        dn = [d for d in drafts if d.symbol == symbol]
        assert dn == [], f"Expected no drafts, got {dn}"
    finally:
        await _cleanup_ticker(symbol)


async def test_cooldown_blocks_repeat():
    """If an open delta_neutral prediction exists for the symbol, a second
    generate() call must return no new draft for that symbol."""
    symbol = f"{_SYM}_COOLDOWN"
    await _insert_ticker(symbol, Decimal("0.0005"))

    try:
        # First call — should emit
        drafts1 = await CashAndCarry(symbols=[symbol]).generate()
        dn1 = [d for d in drafts1 if d.symbol == symbol]
        assert len(dn1) == 1, "First call should emit exactly one draft"

        # Persist the draft as an 'open' prediction so the cooldown gate fires
        draft = dn1[0]
        now = datetime.now(timezone.utc)
        async with shared_session_scope() as session:
            session.add(
                Prediction(
                    id=uuid.uuid4(),
                    strategy_id=draft.strategy_id,
                    strategy_version=draft.strategy_version,
                    generated_at=now,
                    symbol=symbol,
                    exchange=_EXCHANGE,
                    asset_class="crypto",
                    side="delta_neutral",
                    confidence=draft.confidence,
                    horizon_seconds=draft.horizon_seconds,
                    close_by=now + timedelta(seconds=draft.horizon_seconds),
                    entry_price_ref=draft.entry_price_ref,
                    thesis=draft.thesis,
                    context=draft.context,
                    status="open",
                )
            )

        # Second call — cooldown must suppress new draft
        drafts2 = await CashAndCarry(symbols=[symbol]).generate()
        dn2 = [d for d in drafts2 if d.symbol == symbol]
        assert dn2 == [], f"Cooldown should block second emit, got {dn2}"
    finally:
        await _cleanup_ticker(symbol)
        await _cleanup_predictions(symbol)

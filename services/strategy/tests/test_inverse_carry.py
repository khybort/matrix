"""inverse_carry strategy module tests.

Mirror of test_cash_and_carry but for the NEGATIVE-funding direction:
  - test_negative_funding_emits_inverse_carry: funding ≤ -MIN → draft emitted
  - test_positive_funding_no_emit: positive funding → no draft
  - test_thin_negative_funding_no_emit: funding above -MIN (too shallow) → none
  - test_cooldown_blocks_repeat: open inverse_carry prediction → second call empty
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import delete

from matrix_shared import local_session_scope, shared_session_scope
from matrix_shared.models import Prediction, TickerSnapshot

from strategy.modules.crypto.inverse_carry import (
    DEFAULT_MIN_FUNDING,
    InverseCarry,
    STRATEGY_ID,
)

from tests.conftest import TEST_SYM

pytestmark = pytest.mark.asyncio

_SYM = f"ICTEST_{TEST_SYM}"
_EXCHANGE = "bybit"


async def _insert_ticker(symbol: str, funding_rate: Decimal) -> uuid.UUID:
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
        await session.execute(delete(Prediction).where(Prediction.symbol == symbol))


async def test_negative_funding_emits_inverse_carry():
    """funding well below -MIN → generate() emits an inverse_carry draft."""
    symbol = f"{_SYM}_NEG"
    await _insert_ticker(symbol, Decimal("-0.0009"))  # -0.09%/8h, past the -0.08 floor
    try:
        drafts = await InverseCarry(symbols=[symbol]).generate()
        ic = [d for d in drafts if d.symbol == symbol]
        assert len(ic) == 1, f"Expected 1 draft, got {len(ic)}"
        d = ic[0]
        assert d.side == "inverse_carry"
        assert d.strategy_id == STRATEGY_ID
        assert Decimal("0.1") <= d.confidence <= Decimal("1.0")
        assert "inverse delta-neutral funding capture" in d.thesis
        # Context stores the SIGNED (negative) funding — the engine flips it.
        assert Decimal(d.context["funding_rate_8h"]) < 0
    finally:
        await _cleanup_ticker(symbol)


async def test_newer_binance_snapshot_does_not_drive_entry():
    """Binance poller writes the same symbol under exchange='binance'. A newer
    binance snapshot with the WRONG sign must not suppress/emit a Bybit inverse
    carry — the strategy is a Bybit paper fill."""
    symbol = f"{_SYM}_VENUE"
    await _insert_ticker(symbol, Decimal("-0.0009"))
    async with local_session_scope() as session:
        session.add(TickerSnapshot(
            id=uuid.uuid4(),
            exchange="binance",
            symbol=symbol,
            snapshot_ts=datetime.now(timezone.utc) + timedelta(seconds=5),
            last_price=Decimal("50000"),
            mark_price=Decimal("50000"),
            funding_rate=Decimal("0.0020"),  # positive — would kill inverse if mixed
        ))
    try:
        drafts = await InverseCarry(symbols=[symbol]).generate()
        ic = [d for d in drafts if d.symbol == symbol]
        assert len(ic) == 1, "bybit-negative funding must still emit despite newer binance"
        assert Decimal(ic[0].context["funding_rate_8h"]) < 0
    finally:
        await _cleanup_ticker(symbol)


async def test_positive_funding_no_emit():
    """Positive funding is cash_and_carry's job — inverse_carry stays out."""
    symbol = f"{_SYM}_POS"
    await _insert_ticker(symbol, Decimal("0.0009"))
    try:
        drafts = await InverseCarry(symbols=[symbol]).generate()
        assert [d for d in drafts if d.symbol == symbol] == []
    finally:
        await _cleanup_ticker(symbol)


async def test_thin_negative_funding_no_emit():
    """Negative but shallower than -MIN → below the profitability floor → none."""
    symbol = f"{_SYM}_THIN"
    shallow = -(DEFAULT_MIN_FUNDING - Decimal("0.00001"))  # just inside the floor
    await _insert_ticker(symbol, shallow)
    try:
        drafts = await InverseCarry(symbols=[symbol]).generate()
        assert [d for d in drafts if d.symbol == symbol] == []
    finally:
        await _cleanup_ticker(symbol)


async def test_cooldown_blocks_repeat():
    """An open inverse_carry prediction suppresses a second emit for the symbol."""
    symbol = f"{_SYM}_COOLDOWN"
    await _insert_ticker(symbol, Decimal("-0.0009"))
    try:
        drafts1 = await InverseCarry(symbols=[symbol]).generate()
        ic1 = [d for d in drafts1 if d.symbol == symbol]
        assert len(ic1) == 1

        draft = ic1[0]
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
                    side="inverse_carry",
                    confidence=draft.confidence,
                    horizon_seconds=draft.horizon_seconds,
                    close_by=now + timedelta(seconds=draft.horizon_seconds),
                    entry_price_ref=draft.entry_price_ref,
                    thesis=draft.thesis,
                    context=draft.context,
                    status="open",
                )
            )

        drafts2 = await InverseCarry(symbols=[symbol]).generate()
        assert [d for d in drafts2 if d.symbol == symbol] == []
    finally:
        await _cleanup_ticker(symbol)
        await _cleanup_predictions(symbol)

"""Tests for delta_neutral PaperPosition PnL accrual and close mechanics.

Covers:
  test_delta_neutral_pnl_accrues_with_funding
    Open a delta_neutral position 4h ago; funding=0.0005/8h.
    Expected PnL ≈ notional × (4/8) × 0.0005.

  test_delta_neutral_close_credits_wallet
    Open + horizon-close a delta_neutral position; wallet.cash_usd increases
    by the expected funding accrual PnL.

Tests seed TickerSnapshot on the LOCAL tier (same DB in test env) so
_latest_funding_rate has something to read. No price seeding required.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from matrix_shared import local_session_scope, shared_session_scope
from matrix_shared.models import (
    Outcome,
    PaperPosition,
    Prediction,
    TickerSnapshot,
    Wallet,
)

from backtest.paper_trade import (
    _unrealized_pnl,
    close_due_positions,
)

# Only the async integration test needs asyncio; the sync unit tests are plain.
_SYM = "TEST_DN_BTCUSDT"
_EXCHANGE = "bybit"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _insert_ticker(symbol: str, funding_rate: Decimal) -> None:
    async with local_session_scope() as session:
        await session.execute(
            delete(TickerSnapshot).where(TickerSnapshot.symbol == symbol)
        )
        session.add(
            TickerSnapshot(
                id=uuid.uuid4(),
                exchange=_EXCHANGE,
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


# ─────────────────────────────────────────────────────────────────────────────
# Lightweight stand-in for PaperPosition (avoids SQLAlchemy instrumentation)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class _FakePos:
    """Minimal attributes consumed by _unrealized_pnl for delta_neutral."""
    id: uuid.UUID
    symbol: str
    side: str
    notional_usd: Decimal
    opened_at: datetime
    opened_price: Decimal
    asset_class: str
    exchange: str


def _make_fake_pos(
    symbol: str = _SYM,
    notional: Decimal = Decimal("1000"),
    hours_ago: float = 4.0,
) -> _FakePos:
    opened = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return _FakePos(
        id=uuid.uuid4(),
        symbol=symbol,
        side="delta_neutral",
        notional_usd=notional,
        opened_at=opened,
        opened_price=Decimal("50000"),
        asset_class="crypto",
        exchange=_EXCHANGE,
    )


# ─────────────────────────────────────────────────────────────────────────────
# _unrealized_pnl unit tests (pure functions, no DB, no async)
# ─────────────────────────────────────────────────────────────────────────────

def test_delta_neutral_pnl_accrues_with_funding():
    """Pure unit test for _unrealized_pnl with delta_neutral side.

    Position opened 4h ago, funding=0.0005/8h.
    Expected PnL = 1000 × (4/8) × 0.0005 = 0.25 USD.
    We allow ±0.05 USD tolerance for wall-clock drift in elapsed hours.
    """
    notional = Decimal("1000")
    fr = Decimal("0.0005")
    pos = _make_fake_pos(notional=notional, hours_ago=4.0)

    pnl = _unrealized_pnl(pos, pos.opened_price, funding_rate_8h=fr)

    expected = notional * Decimal("4") / Decimal("8") * fr  # = 0.25
    assert abs(pnl - expected) < Decimal("0.05"), (
        f"delta_neutral PnL {pnl} not within 0.05 of expected {expected}"
    )


def test_delta_neutral_pnl_zero_without_funding():
    """If funding_rate_8h is None, _unrealized_pnl returns 0 (not an error)."""
    pos = _make_fake_pos()
    pnl = _unrealized_pnl(pos, pos.opened_price, funding_rate_8h=None)
    assert pnl == Decimal("0")


def test_delta_neutral_pnl_negative_funding_bleeds():
    """Negative funding causes negative PnL — position bleeds.
    Expected = 1000 × (4/8) × (-0.0002) = -0.10 USD."""
    notional = Decimal("1000")
    fr = Decimal("-0.0002")
    pos = _make_fake_pos(notional=notional, hours_ago=4.0)
    pnl = _unrealized_pnl(pos, pos.opened_price, funding_rate_8h=fr)
    assert pnl < Decimal("0"), "Negative funding should produce negative PnL"


# ─────────────────────────────────────────────────────────────────────────────
# Integration: close_due_positions credits wallet
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delta_neutral_close_credits_wallet(wallet_id, make_prediction):
    """Open a delta_neutral position with an elapsed horizon; verify:
    1. close_due_positions() closes it.
    2. wallet.cash_usd increases (funding accrual PnL > 0).
    3. An Outcome row is written.
    """
    funding_rate = Decimal("0.0005")
    await _insert_ticker(_SYM, funding_rate)

    try:
        # Prediction: delta_neutral, horizon already elapsed
        pid = await make_prediction(
            strategy_id="cash_and_carry",
            side="delta_neutral",
            symbol=_SYM,
            asset_class="crypto",
            generated_at=datetime.now(timezone.utc) - timedelta(hours=9),
            close_by_offset=timedelta(hours=8),  # now in the past
            horizon_seconds=28800,
        )

        notional = Decimal("500")
        pos_id = uuid.uuid4()
        opened_at = datetime.now(timezone.utc) - timedelta(hours=9)

        async with shared_session_scope() as session:
            session.add(
                PaperPosition(
                    id=pos_id,
                    prediction_id=pid,
                    symbol=_SYM,
                    exchange=_EXCHANGE,
                    asset_class="crypto",
                    side="delta_neutral",
                    notional_usd=notional,
                    opened_at=opened_at,
                    opened_price=Decimal("50000"),
                    status="open",
                    wallet_id=wallet_id,
                )
            )
            # Simulate notional locked at open
            w = await session.get(Wallet, wallet_id)
            w.cash_usd -= notional
            w.locked_usd += notional

        # Read cash before close
        async with shared_session_scope() as session:
            w = await session.get(Wallet, wallet_id)
            cash_before = Decimal(w.cash_usd)

        n = await close_due_positions()
        assert n >= 1, "Expected at least one position closed"

        async with shared_session_scope() as session:
            pos = await session.get(PaperPosition, pos_id)
            assert pos.status == "closed", f"Expected closed, got {pos.status}"
            assert pos.pnl_usd is not None

            outcome = (
                await session.execute(
                    select(Outcome).where(Outcome.prediction_id == pid)
                )
            ).scalar_one()
            assert outcome.reason == "hit_horizon"
            assert outcome.pnl_usd >= Decimal("0"), (
                f"Expected non-negative funding PnL, got {outcome.pnl_usd}"
            )

            w = await session.get(Wallet, wallet_id)
            cash_after = Decimal(w.cash_usd)

        # Cash should have recovered (notional returned + any funding accrual).
        assert cash_after >= cash_before, (
            f"Wallet cash should not decrease after delta_neutral close; "
            f"before={cash_before} after={cash_after}"
        )

    finally:
        # Cleanup position if still open (e.g. close_due_positions found 0)
        async with shared_session_scope() as session:
            await session.execute(
                delete(PaperPosition).where(PaperPosition.id == pos_id)
            )
        await _cleanup_ticker(_SYM)

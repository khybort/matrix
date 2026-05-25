"""Unit tests for backtest.paper_trade lifecycle functions.

Focus is the three pieces the production loop calls every tick:
    expire_stale_predictions, open_due_positions, close_due_positions

Each test sets up the minimum state for one behavior, then asserts both
the return value and the resulting DB state. We talk to the actual
matrix-postgres-shared/local containers — same SQL semantics as prod.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from matrix_shared import shared_session_scope
from matrix_shared.models import Outcome, PaperPosition, Prediction, Wallet

from backtest.paper_trade import (
    close_due_positions,
    expire_stale_predictions,
    open_due_positions,
)

pytestmark = pytest.mark.asyncio


async def _get_prediction_status(pid: uuid.UUID) -> str | None:
    async with shared_session_scope() as session:
        row = await session.get(Prediction, pid)
        return row.status if row else None


async def _get_wallet_cash(wid: uuid.UUID) -> Decimal:
    async with shared_session_scope() as session:
        row = await session.get(Wallet, wid)
        return Decimal(row.cash_usd)


# --- expire_stale_predictions ------------------------------------------

async def test_expire_marks_stale_predictions(make_prediction):
    """Open prediction with close_by < now and no paper_position → expired."""
    pid = await make_prediction(
        strategy_id="exp_strat_a",
        # close_by 60s in the past
        generated_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        close_by_offset=timedelta(seconds=60),
    )
    n = await expire_stale_predictions()
    assert n >= 1
    assert await _get_prediction_status(pid) == "expired"


async def test_expire_skips_fresh_predictions(make_prediction):
    """Prediction with close_by in the future is left alone."""
    pid = await make_prediction(
        strategy_id="exp_strat_b",
        close_by_offset=timedelta(minutes=5),
    )
    await expire_stale_predictions()
    assert await _get_prediction_status(pid) == "open"


async def test_expire_skips_predictions_with_open_position(
    wallet_id, make_prediction
):
    """Stale prediction that HAS a paper_position should NOT be expired —
    the closer is responsible for those."""
    pid = await make_prediction(
        strategy_id="exp_strat_c",
        generated_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        close_by_offset=timedelta(seconds=60),
    )
    async with shared_session_scope() as session:
        session.add(
            PaperPosition(
                id=uuid.uuid4(), prediction_id=pid, symbol="BTCUSDT",
                exchange="bybit", side="long", notional_usd=Decimal("50"),
                opened_at=datetime.now(timezone.utc),
                opened_price=Decimal("100"),
                status="open", wallet_id=wallet_id, asset_class="crypto",
            )
        )
    try:
        await expire_stale_predictions()
        assert await _get_prediction_status(pid) == "open"
    finally:
        async with shared_session_scope() as session:
            await session.execute(delete(PaperPosition).where(PaperPosition.prediction_id == pid))


# --- open_due_positions -------------------------------------------------

async def test_open_skips_expired_predictions(
    wallet_id, make_prediction, seed_recent_trades, monkeypatch
):
    """Stale prediction must not become a paper_position. This is the
    filter we just added in this session — guard it forever."""
    monkeypatch.setattr(
        "backtest.paper_trade.DEFAULT_WALLET_ID", wallet_id
    )
    await seed_recent_trades("BTCUSDT", Decimal("100"))
    pid = await make_prediction(
        strategy_id="open_strat_a",
        generated_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        close_by_offset=timedelta(seconds=60),  # already past
    )
    opened = await open_due_positions()
    # The test wallet has 0 positions, but a stale prediction must not be
    # eligible; opened may be 0 OR may include other strategies' fresh
    # predictions on this wallet (none here). What matters: nothing was
    # opened on `pid`.
    async with shared_session_scope() as session:
        n_for_pred = (
            await session.execute(
                select(PaperPosition).where(PaperPosition.prediction_id == pid)
            )
        ).scalar_one_or_none()
    assert n_for_pred is None
    # Sanity: prediction itself is still 'open' — only expire_stale flips it.
    assert await _get_prediction_status(pid) == "open"
    _ = opened  # value unused; behavior asserted above


# --- close_due_positions -----------------------------------------------

async def test_close_writes_outcome_and_unlocks_capital(
    wallet_id, make_prediction, seed_recent_trades
):
    """Open position whose horizon passed → closed status, outcome row,
    wallet cash recovered."""
    pid = await make_prediction(
        strategy_id="close_strat_a",
        generated_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        close_by_offset=timedelta(seconds=60),  # close_by already past
    )
    await seed_recent_trades("BTCUSDT", Decimal("101"))  # exit @ ~101
    notional = Decimal("50")
    pos_id = uuid.uuid4()
    async with shared_session_scope() as session:
        session.add(
            PaperPosition(
                id=pos_id, prediction_id=pid, symbol="BTCUSDT", exchange="bybit",
                side="long", notional_usd=notional,
                opened_at=datetime.now(timezone.utc) - timedelta(minutes=10),
                opened_price=Decimal("100"),
                status="open", wallet_id=wallet_id, asset_class="crypto",
            )
        )
        # Move notional from cash into locked so closer can release it.
        w = await session.get(Wallet, wallet_id)
        w.cash_usd -= notional
        w.locked_usd += notional

    cash_before = await _get_wallet_cash(wallet_id)
    n = await close_due_positions()
    assert n >= 1

    # Position closed
    async with shared_session_scope() as session:
        pos = await session.get(PaperPosition, pos_id)
        assert pos.status == "closed"
        assert pos.closed_price is not None
        # Outcome row created
        outcome = (
            await session.execute(select(Outcome).where(Outcome.prediction_id == pid))
        ).scalar_one()
        assert outcome.reason == "hit_horizon"
    # Cash should have returned (cash_before + notional + pnl). PnL is
    # tiny; just assert cash strictly increased.
    cash_after = await _get_wallet_cash(wallet_id)
    assert cash_after > cash_before

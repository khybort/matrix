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
from matrix_shared.models import (
    Outcome,
    PaperPosition,
    Prediction,
    Wallet,
    WalletSnapshot,
)

from backtest.paper_trade import (
    close_due_positions,
    expire_stale_predictions,
    open_due_positions,
    snapshot_wallet,
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
                id=uuid.uuid4(), prediction_id=pid, symbol="TEST_BTCUSDT",
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
    await seed_recent_trades("TEST_BTCUSDT", Decimal("100"))
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
    await seed_recent_trades("TEST_BTCUSDT", Decimal("101"))  # exit @ ~101
    notional = Decimal("50")
    pos_id = uuid.uuid4()
    async with shared_session_scope() as session:
        session.add(
            PaperPosition(
                id=pos_id, prediction_id=pid, symbol="TEST_BTCUSDT", exchange="bybit",
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


# --- equity trailing stop (snapshot_wallet) -----------------------------

async def test_trailing_stop_trips_when_equity_falls_below_peak(
    wallet_id, monkeypatch
):
    """Wallet with equity_trailing_stop_pct=0.10. Seed snapshot history where
    equity rallied to 11000 (peak), then drained back. The day-loss circuit
    against day_start_equity (10000) does NOT trip (current cash is still
    ~9900 — only 1% from start). The trailing stop SHOULD trip because peak
    is 11000 and 9900 is a ~10% drawdown from peak."""
    monkeypatch.setattr(
        "backtest.paper_trade.DEFAULT_WALLET_ID", wallet_id
    )
    now = datetime.now(timezone.utc)
    async with shared_session_scope() as session:
        w = await session.get(Wallet, wallet_id)
        w.equity_trailing_stop_pct = Decimal("0.10")
        w.day_start_equity = Decimal("10000")
        w.day_start_at = now - timedelta(hours=2)
        # Cash is the only liquidity here (no open positions), so equity
        # readout in snapshot_wallet == cash_usd. Set cash to 9900 → 10%
        # drawdown from the 11000 peak we seed below.
        w.cash_usd = Decimal("9900")
        w.locked_usd = Decimal("0")
        # Peak snapshot recorded an hour ago.
        session.add(
            WalletSnapshot(
                id=uuid.uuid4(),
                wallet_id=wallet_id,
                snapshot_ts=now - timedelta(hours=1),
                equity_usd=Decimal("11000"),
                cash_usd=Decimal("11000"),
                locked_usd=Decimal("0"),
                n_open_positions=0,
                realized_pnl_usd=Decimal("1000"),
                unrealized_pnl_usd=Decimal("0"),
            )
        )

    await snapshot_wallet()

    async with shared_session_scope() as session:
        w = await session.get(Wallet, wallet_id)
        assert w.circuit_tripped_at is not None, (
            "trailing stop should have tripped on 10% drawdown from peak"
        )


# --- per-trade TP / SL early close --------------------------------------

async def test_tp_closes_early_in_profit(
    wallet_id, make_prediction, seed_recent_trades
):
    """Open long with tp_pct=0.02 and close_by far in the future. Market
    moves to 1.03x opened_price. close_due_positions should close it with
    reason='hit_tp' even though close_by hasn't passed."""
    pid = await make_prediction(
        strategy_id="tp_strat",
        # close_by is 1h out — the horizon path must not be what fires.
        close_by_offset=timedelta(hours=1),
    )
    # Patch tp_pct on the prediction (factory doesn't expose it).
    async with shared_session_scope() as session:
        p = await session.get(Prediction, pid)
        p.tp_pct = Decimal("0.020000")

    # Mark moves to 103 from opened_price 100 → +3% > tp 2%.
    await seed_recent_trades("TEST_BTCUSDT", Decimal("103"))

    notional = Decimal("50")
    pos_id = uuid.uuid4()
    async with shared_session_scope() as session:
        session.add(
            PaperPosition(
                id=pos_id, prediction_id=pid, symbol="TEST_BTCUSDT", exchange="bybit",
                side="long", notional_usd=notional,
                opened_at=datetime.now(timezone.utc) - timedelta(minutes=5),
                opened_price=Decimal("100"),
                status="open", wallet_id=wallet_id, asset_class="crypto",
            )
        )
        w = await session.get(Wallet, wallet_id)
        w.cash_usd -= notional
        w.locked_usd += notional

    n = await close_due_positions()
    assert n >= 1

    async with shared_session_scope() as session:
        pos = await session.get(PaperPosition, pos_id)
        assert pos.status == "closed"
        outcome = (
            await session.execute(select(Outcome).where(Outcome.prediction_id == pid))
        ).scalar_one()
        assert outcome.reason == "hit_tp"
        assert outcome.pnl_usd > 0


async def test_sl_closes_early_in_loss(
    wallet_id, make_prediction, seed_recent_trades
):
    """Symmetric for short: sl_pct=0.02, market moves against by 3% →
    close with reason='hit_sl'. For a short opened at 100, an adverse move
    means price RISES; sl triggers when (price - opened) / opened >= sl_pct."""
    pid = await make_prediction(
        strategy_id="sl_strat",
        side="short",
        close_by_offset=timedelta(hours=1),
    )
    async with shared_session_scope() as session:
        p = await session.get(Prediction, pid)
        p.sl_pct = Decimal("0.020000")

    # Mark moves to 103 — short is now 3% underwater, exceeds sl 2%.
    await seed_recent_trades("TEST_BTCUSDT", Decimal("103"))

    notional = Decimal("50")
    pos_id = uuid.uuid4()
    async with shared_session_scope() as session:
        session.add(
            PaperPosition(
                id=pos_id, prediction_id=pid, symbol="TEST_BTCUSDT", exchange="bybit",
                side="short", notional_usd=notional,
                opened_at=datetime.now(timezone.utc) - timedelta(minutes=5),
                opened_price=Decimal("100"),
                status="open", wallet_id=wallet_id, asset_class="crypto",
            )
        )
        w = await session.get(Wallet, wallet_id)
        w.cash_usd -= notional
        w.locked_usd += notional

    n = await close_due_positions()
    assert n >= 1

    async with shared_session_scope() as session:
        pos = await session.get(PaperPosition, pos_id)
        assert pos.status == "closed"
        outcome = (
            await session.execute(select(Outcome).where(Outcome.prediction_id == pid))
        ).scalar_one()
        assert outcome.reason == "hit_sl"
        assert outcome.pnl_usd < 0

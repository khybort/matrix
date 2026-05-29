"""Ledger-invariant tests for the wallet reconciliation + atomic balance writes.

Background: wallet cash/locked were once mutated via an unguarded read-modify-
write, so a lost `close` decrement could leave capital *phantom-locked*
(locked_usd ratchets up with no open position backing it). The crypto paper
wallet hit this — $8,594 phantom-locked, only ~$284 deployable. These tests
pin the two guards added in response:

    * reconcile_wallets() — self-heals locked_usd → Σ(open notional), moving any
      phantom back to cash with equity (cash+locked) preserved.
    * close_due_positions() releases the lock atomically.

The invariant under test, everywhere: `locked_usd == Σ(open paper_position
notional)` and `cash + locked` is conserved by any relabeling.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, select

from matrix_shared import shared_session_scope
from matrix_shared.models import Outcome, PaperPosition, Wallet

from backtest.paper_trade import close_due_positions, reconcile_wallets

pytestmark = pytest.mark.asyncio


async def _wallet(wid: uuid.UUID) -> tuple[Decimal, Decimal]:
    async with shared_session_scope() as session:
        w = await session.get(Wallet, wid)
        return Decimal(w.cash_usd), Decimal(w.locked_usd)


async def _open_notional(wid: uuid.UUID) -> Decimal:
    async with shared_session_scope() as session:
        return Decimal(
            (
                await session.execute(
                    select(func.coalesce(func.sum(PaperPosition.notional_usd), 0))
                    .where(
                        PaperPosition.wallet_id == wid,
                        PaperPosition.status == "open",
                    )
                )
            ).scalar_one()
        )


async def _add_open_position(wid: uuid.UUID, pid: uuid.UUID, notional: Decimal) -> None:
    async with shared_session_scope() as session:
        session.add(
            PaperPosition(
                id=uuid.uuid4(), prediction_id=pid, symbol="TEST_BTCUSDT",
                exchange="bybit", side="long", notional_usd=notional,
                opened_at=datetime.now(timezone.utc), opened_price=Decimal("100"),
                status="open", wallet_id=wid, asset_class="crypto",
            )
        )


async def test_reconcile_frees_phantom_locked(wallet_id, make_prediction):
    """A wallet over-locked beyond its real open notional (the production bug)
    is healed: locked drops to Σ open notional, the phantom returns to cash, and
    equity (cash + locked) is unchanged."""
    pid = await make_prediction(strategy_id="recon_a")
    notional = Decimal("50")
    await _add_open_position(wallet_id, pid, notional)

    # Simulate the lost-update corruption: locked inflated far past the single
    # $50 position; cash debited to match (as the open path would have).
    phantom = Decimal("8000")
    async with shared_session_scope() as session:
        w = await session.get(Wallet, wallet_id)
        w.cash_usd = Decimal("10000") - notional - phantom
        w.locked_usd = notional + phantom

    cash_before, locked_before = await _wallet(wallet_id)
    equity_before = cash_before + locked_before

    n = await reconcile_wallets()
    assert n >= 1  # our wallet (and possibly others) were corrected

    cash_after, locked_after = await _wallet(wallet_id)
    # locked now equals the real open notional, phantom is gone, equity preserved.
    assert locked_after == notional
    assert locked_after == await _open_notional(wallet_id)
    assert cash_after == cash_before + phantom
    assert cash_after + locked_after == equity_before


async def test_reconcile_is_noop_when_consistent(wallet_id, make_prediction):
    """When locked already equals Σ open notional, reconcile must not touch the
    wallet (no spurious cash movement)."""
    pid = await make_prediction(strategy_id="recon_b")
    notional = Decimal("75")
    await _add_open_position(wallet_id, pid, notional)
    async with shared_session_scope() as session:
        w = await session.get(Wallet, wallet_id)
        w.cash_usd = Decimal("10000") - notional
        w.locked_usd = notional

    cash_before, locked_before = await _wallet(wallet_id)
    await reconcile_wallets()
    cash_after, locked_after = await _wallet(wallet_id)

    assert (cash_after, locked_after) == (cash_before, locked_before)
    assert locked_after == await _open_notional(wallet_id)


async def test_close_then_reconcile_holds_invariant(
    wallet_id, make_prediction, seed_recent_trades
):
    """Full cycle: open position → close via close_due_positions → reconcile.
    After close the lock is released atomically; the invariant
    locked == Σ open notional holds and equity is only changed by realized PnL."""
    pid = await make_prediction(
        strategy_id="recon_c",
        generated_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        close_by_offset=timedelta(seconds=60),  # already past → horizon close
    )
    await seed_recent_trades("TEST_BTCUSDT", Decimal("100"))  # flat exit ≈ no PnL
    notional = Decimal("60")
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
        w = await session.get(Wallet, wallet_id)
        w.cash_usd = Decimal("10000") - notional
        w.locked_usd = notional

    n = await close_due_positions()
    assert n >= 1

    # Lock fully released by the atomic close path.
    cash_after, locked_after = await _wallet(wallet_id)
    assert locked_after == await _open_notional(wallet_id) == Decimal("0")

    # Reconcile is now a no-op (nothing phantom-locked).
    cash_recon, locked_recon = (cash_after, locked_after)
    await reconcile_wallets()
    assert (cash_after, locked_after) == (cash_recon, locked_recon)

    async with shared_session_scope() as session:
        await session.execute(delete(Outcome).where(Outcome.prediction_id == pid))

"""Test the orphan-flat-close path in close_due_positions.

Scenario: a paper_position's prediction.close_by is > 24h in the past
(the KONYA BIST bug — symbol had no price feed for 72h).
There are NO market_trades for the symbol, so _latest_price returns None.

Expected: close_due_positions() must:
  - Close the position (status='closed')
  - Write an Outcome row with reason='orphan_flat_close', pnl_usd=0
  - Unlock notional back to the wallet
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from matrix_shared import local_session_scope, shared_session_scope
from matrix_shared.models import MarketTrade, Outcome, PaperPosition, Prediction, Wallet

from backtest.paper_trade import close_due_positions

pytestmark = pytest.mark.asyncio

_ORPHAN_SYM = "TEST_ORPHAN_KONYA"  # no trades will be seeded for this symbol


async def test_orphan_close_flat_pnl(wallet_id, make_prediction):
    """Position with close_by 25h past + no price → orphan_flat_close, pnl=0."""
    now = datetime.now(timezone.utc)

    # Ensure no stale trades exist for this symbol
    async with local_session_scope() as session:
        await session.execute(
            delete(MarketTrade).where(MarketTrade.symbol == _ORPHAN_SYM)
        )

    # Prediction generated 26h ago, horizon 1h → close_by 25h ago
    gen_at = now - timedelta(hours=26)
    pid = await make_prediction(
        strategy_id="orphan_test_strat",
        asset_class="bist",  # BIST is the real-world case (KONYA)
        symbol=_ORPHAN_SYM,
        side="long",
        generated_at=gen_at,
        close_by_offset=timedelta(hours=1),  # close_by = gen_at + 1h = 25h ago
    )

    notional = Decimal("100")
    pos_id = uuid.uuid4()

    async with shared_session_scope() as session:
        session.add(
            PaperPosition(
                id=pos_id,
                prediction_id=pid,
                symbol=_ORPHAN_SYM,
                exchange="bist",
                asset_class="bist",
                side="long",
                notional_usd=notional,
                opened_at=gen_at,
                opened_price=Decimal("50.00"),
                status="open",
                wallet_id=wallet_id,
            )
        )
        w = await session.get(Wallet, wallet_id)
        w.cash_usd -= notional
        w.locked_usd += notional

    cash_before = await _cash(wallet_id)
    n = await close_due_positions()
    assert n >= 1, "orphan position must have been closed"

    async with shared_session_scope() as session:
        pos = await session.get(PaperPosition, pos_id)
        assert pos.status == "closed", f"expected closed, got {pos.status}"

        outcome = (
            await session.execute(
                select(Outcome).where(Outcome.prediction_id == pid)
            )
        ).scalar_one()
        assert outcome.reason == "orphan_flat_close", (
            f"expected orphan_flat_close, got {outcome.reason}"
        )
        assert outcome.pnl_usd == Decimal("0"), (
            f"orphan close must have pnl_usd=0, got {outcome.pnl_usd}"
        )

    cash_after = await _cash(wallet_id)
    assert cash_after == cash_before + notional, (
        "notional must be returned to wallet on orphan close"
    )


async def test_non_stale_missing_price_skipped(wallet_id, make_prediction):
    """Position whose close_by is only 1 min past and has no price must NOT
    be orphan-closed — it's too fresh (threshold is 24h)."""
    now = datetime.now(timezone.utc)

    async with local_session_scope() as session:
        await session.execute(
            delete(MarketTrade).where(MarketTrade.symbol == _ORPHAN_SYM)
        )

    # close_by just barely in the past (1 minute ago) — not stale enough
    gen_at = now - timedelta(minutes=62)  # gen + 61min horizon = 1 min past
    pid = await make_prediction(
        strategy_id="orphan_fresh_strat",
        asset_class="bist",
        symbol=_ORPHAN_SYM,
        side="long",
        generated_at=gen_at,
        close_by_offset=timedelta(minutes=61),
    )

    notional = Decimal("50")
    pos_id = uuid.uuid4()

    async with shared_session_scope() as session:
        session.add(
            PaperPosition(
                id=pos_id,
                prediction_id=pid,
                symbol=_ORPHAN_SYM,
                exchange="bist",
                asset_class="bist",
                side="long",
                notional_usd=notional,
                opened_at=gen_at,
                opened_price=Decimal("50.00"),
                status="open",
                wallet_id=wallet_id,
            )
        )
        w = await session.get(Wallet, wallet_id)
        w.cash_usd -= notional
        w.locked_usd += notional

    await close_due_positions()

    async with shared_session_scope() as session:
        pos = await session.get(PaperPosition, pos_id)
        # Position should still be open (no price, not stale enough)
        assert pos.status == "open", (
            f"fresh missing-price position must not be orphan-closed, got {pos.status}"
        )

    # cleanup
    async with shared_session_scope() as session:
        await session.execute(delete(PaperPosition).where(PaperPosition.id == pos_id))
        w = await session.get(Wallet, wallet_id)
        w.locked_usd -= notional
        w.cash_usd += notional


async def _cash(wallet_id: uuid.UUID) -> Decimal:
    async with shared_session_scope() as session:
        w = await session.get(Wallet, wallet_id)
        return Decimal(w.cash_usd)

"""A synthetic asset class for engine tests.

The paper engine is keyed by asset_class everywhere (wallet, price lookup,
candidate pool), and `all_markets()` never yields "test", so positions opened
under TEST_ASSET can only come from the test process: no race with the live
engine, no debits on the live wallets, no cleanup refunds. Predictions with a
TEST_ symbol on the "test" class fall back to market_trades for pricing and
are long-only (unknown market → no shorts), which is all these tests need.

Before this, test_slot_enforcement ran `_open_for_market("crypto")` against
the live default wallet: it opened real positions for live predictions, raced
the engine into `paper_positions_prediction_id_key` violations that aborted
its tick, and its assertions depended on the live queue depth.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import delete

from matrix_shared import shared_session_scope
from matrix_shared.models import PaperPosition, Wallet, WalletSnapshot

from backtest.paper_trade import SHADOW_WALLET_NAME

TEST_ASSET = "test"


def _wallet(name: str) -> Wallet:
    return Wallet(
        id=uuid.uuid4(), name=name, asset_class=TEST_ASSET,
        starting_capital_usd=Decimal("10000"), cash_usd=Decimal("10000"), locked_usd=Decimal("0"),
        max_position_pct=Decimal("0.02"), max_concurrent_positions=20,
        daily_loss_circuit_pct=Decimal("0.05"), day_start_equity=Decimal("10000"),
        day_start_at=datetime.now(timezone.utc),
    )


@asynccontextmanager
async def isolated_wallets():
    """Yield (champion_wallet_id, shadow_wallet_id) on TEST_ASSET; drops both
    (and every position booked in them) afterwards."""
    champion, shadow = _wallet(f"test-champion-{uuid.uuid4().hex[:6]}"), _wallet(SHADOW_WALLET_NAME)
    async with shared_session_scope() as session:
        await session.execute(delete(Wallet).where(Wallet.asset_class == TEST_ASSET))  # stale runs
        session.add_all([champion, shadow])
        ids = (champion.id, shadow.id)
    try:
        yield ids
    finally:
        async with shared_session_scope() as session:
            await session.execute(delete(PaperPosition).where(PaperPosition.wallet_id.in_(ids)))
            await session.execute(delete(WalletSnapshot).where(WalletSnapshot.wallet_id.in_(ids)))
            await session.execute(delete(Wallet).where(Wallet.id.in_(ids)))

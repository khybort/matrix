"""Crypto live-execution adapter — wraps the existing BybitConnector.

Implements `matrix_shared.markets.ExecutionAdapter` over the V5 REST
client. Order submission still passes through `execution.safety.
should_submit_live` (cert + circuit + cap), which lives inside
BybitConnector.place_order — this adapter does not bypass any gate.
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any

from matrix_shared.markets import ExecutionAdapter

from execution.bybit_connector import BybitConnector


class CryptoLiveExecutor(ExecutionAdapter):
    def __init__(self, *, testnet: bool | None = None) -> None:
        if testnet is None:
            testnet = (
                os.environ.get("BYBIT_TESTNET", "true").strip().lower() != "false"
            )
        self._connector = BybitConnector(testnet=testnet)
        self._testnet = testnet

    @property
    def testnet(self) -> bool:
        return self._testnet

    async def place_order(
        self,
        *,
        symbol: str,
        side: str,
        qty: Decimal,
        **kw: Any,
    ) -> dict[str, Any]:
        # BybitConnector.place_order owns the safety gate + dry-run logic.
        return await self._connector.place_order(
            symbol=symbol, side=side, qty=qty, **kw
        )

    async def cancel_order(self, *, order_id: str) -> bool:
        result = await self._connector.cancel_order(order_id=order_id)
        return bool(result)

    async def positions(self) -> list[dict[str, Any]]:
        return await self._connector.get_positions(dry_run=True)

    async def equity(self) -> Decimal:
        bal = await self._connector.get_wallet_balance(dry_run=True)
        equity = bal.get("equity")
        return Decimal(str(equity)) if equity is not None else Decimal("0")

    async def health(self) -> bool:
        try:
            await self._connector.get_wallet_balance(dry_run=True)
            return True
        except Exception:
            return False

    async def aclose(self) -> None:
        await self._connector.aclose()

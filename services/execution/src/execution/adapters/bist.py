"""BIST live-execution adapter — Phase 0 placeholder.

Every method raises NotImplementedError. BIST live execution is a
Phase 1 deliverable: pick a broker (AlgoLab / İş Yatırım API / Garanti
API), wire HMAC auth + order placement + position polling, and replace
this stub. Paper-trade simulation for BIST already runs through
services/backtest's PaperPosition engine — it does NOT need an adapter
here.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, NoReturn

from matrix_shared.markets import ExecutionAdapter


def _wall(method: str) -> NoReturn:
    raise NotImplementedError(
        f"BistLiveExecutor.{method}: BIST live broker not wired (Phase 1). "
        "Paper PnL still tracked via services/backtest paper_trade."
    )


class BistLiveExecutor(ExecutionAdapter):
    async def place_order(
        self,
        *,
        symbol: str,
        side: str,
        qty: Decimal,
        **kw: Any,
    ) -> dict[str, Any]:
        _wall("place_order")

    async def cancel_order(self, *, order_id: str) -> bool:
        _wall("cancel_order")

    async def positions(self) -> list[dict[str, Any]]:
        _wall("positions")

    async def equity(self) -> Decimal:
        _wall("equity")

    async def health(self) -> bool:
        # Health = "wired"? Always False until Phase 1.
        return False

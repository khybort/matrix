"""US live-execution adapter — Alpaca REST (paper by default).

Unlike the BIST stub, this is a *real* adapter: when `ALPACA_API_KEY_ID` /
`ALPACA_API_SECRET_KEY` are set it talks to Alpaca's REST API. With no creds
it degrades gracefully — `health()` returns False and the execution daemon
reports it as "not authenticated" rather than crashing. This means adding
creds to `.env.local` flips US execution on with zero code change.

Safety: `place_order` ALWAYS consults `execution.safety.should_submit_live`
first and ABORTS when the gate is closed — exactly like the crypto path.
`ALPACA_PAPER` defaults to true, so even a fully-wired node submits to
Alpaca's paper endpoint until an operator explicitly opts into live capital
*and* the strategy holds a valid `paper_trade_certificate`.
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx
from loguru import logger

from matrix_shared.markets import ExecutionAdapter

from execution.safety import should_submit_live

_PAPER_BASE = "https://paper-api.alpaca.markets"
_LIVE_BASE = "https://api.alpaca.markets"


def _is_paper() -> bool:
    return os.environ.get("ALPACA_PAPER", "true").strip().lower() != "false"


class UsAlpacaExecutor(ExecutionAdapter):
    def __init__(self) -> None:
        self._key = os.environ.get("ALPACA_API_KEY_ID", "").strip()
        self._secret = os.environ.get("ALPACA_API_SECRET_KEY", "").strip()
        self._paper = _is_paper()
        self._base = _PAPER_BASE if self._paper else _LIVE_BASE
        self._client: httpx.AsyncClient | None = None

    @property
    def paper(self) -> bool:
        return self._paper

    @property
    def has_creds(self) -> bool:
        return bool(self._key and self._secret)

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base,
                timeout=httpx.Timeout(15.0),
                headers={
                    "APCA-API-KEY-ID": self._key,
                    "APCA-API-SECRET-KEY": self._secret,
                },
            )
        return self._client

    async def place_order(
        self,
        *,
        symbol: str,
        side: str,
        qty: Decimal,
        **kw: Any,
    ) -> dict[str, Any]:
        # Live gate first — never submit real capital past a closed gate.
        gate = await should_submit_live(
            strategy_id=str(kw.get("strategy_id", "")),
            asset_class=str(kw.get("asset_class", "us")),
            strategy_version=int(kw.get("strategy_version", 0)),
            intended_notional_usd=Decimal(str(kw.get("intended_notional_usd", "0"))),
            wallet_id=UUID(str(kw["wallet_id"])) if kw.get("wallet_id") else UUID(int=0),
        )
        if not gate.allowed:
            logger.warning(
                f"[us] order BLOCKED for {symbol}: {'; '.join(gate.reasons)}"
            )
            return {"submitted": False, "reasons": gate.reasons, "snapshot": gate.snapshot}

        if not self.has_creds:
            return {"submitted": False, "reasons": ["alpaca creds not configured"]}

        # Alpaca uses long-only `buy`/`sell`; shorting is `sell` on a flat/long
        # account. We map our long/short intent to buy/sell at the qty level.
        alpaca_side = "buy" if side == "long" else "sell"
        body = {
            "symbol": symbol,
            "qty": str(qty),
            "side": alpaca_side,
            "type": kw.get("order_type", "market"),
            "time_in_force": kw.get("time_in_force", "day"),
        }
        resp = await self._http().post("/v2/orders", json=body)
        resp.raise_for_status()
        order = resp.json()
        logger.info(
            f"[us] order submitted ({'paper' if self._paper else 'LIVE'}): "
            f"{alpaca_side} {qty} {symbol} id={order.get('id')}"
        )
        return {"submitted": True, "order": order}

    async def cancel_order(self, *, order_id: str) -> bool:
        if not self.has_creds:
            return False
        resp = await self._http().delete(f"/v2/orders/{order_id}")
        return resp.status_code in (200, 204)

    async def positions(self) -> list[dict[str, Any]]:
        if not self.has_creds:
            return []
        resp = await self._http().get("/v2/positions")
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []

    async def equity(self) -> Decimal:
        if not self.has_creds:
            return Decimal("0")
        resp = await self._http().get("/v2/account")
        resp.raise_for_status()
        eq = resp.json().get("equity")
        return Decimal(str(eq)) if eq is not None else Decimal("0")

    async def health(self) -> bool:
        if not self.has_creds:
            return False
        try:
            resp = await self._http().get("/v2/account")
            return resp.status_code == 200
        except Exception:
            return False

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

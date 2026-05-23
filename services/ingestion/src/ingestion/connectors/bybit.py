"""Bybit V5 public WebSocket connector.

Subscribes to linear (USDT-perp) public trade streams. Testnet by default.

Docs: https://bybit-exchange.github.io/docs/v5/ws/public
Testnet linear: wss://stream-testnet.bybit.com/v5/public/linear
Mainnet linear: wss://stream.bybit.com/v5/public/linear
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import orjson
import websockets
from loguru import logger

PING_INTERVAL_S = 20  # Bybit requires app-level ping every <30s


@dataclass(slots=True)
class TradePrint:
    exchange: str
    exchange_trade_id: str
    symbol: str
    trade_ts: datetime
    side: str
    price: Decimal
    size: Decimal


class BybitConnector:
    """Streams public trade prints from Bybit V5 WebSocket.

    Args:
        symbols: iterable of symbols, e.g. ["BTCUSDT", "ETHUSDT"]
        testnet: True (default) → testnet endpoint
    """

    def __init__(self, symbols: Iterable[str], *, testnet: bool = True) -> None:
        self.symbols = list(symbols)
        self.testnet = testnet
        self.url = (
            "wss://stream-testnet.bybit.com/v5/public/linear"
            if testnet
            else "wss://stream.bybit.com/v5/public/linear"
        )

    async def stream(self) -> AsyncIterator[TradePrint]:
        """Yield TradePrints forever; reconnects on disconnect with backoff."""
        backoff = 1.0
        while True:
            try:
                async for trade in self._connect_once():
                    backoff = 1.0  # reset on successful message
                    yield trade
            except (websockets.ConnectionClosed, OSError) as e:
                logger.warning(f"bybit ws disconnect: {e}; retry in {backoff:.1f}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _connect_once(self) -> AsyncIterator[TradePrint]:
        async with websockets.connect(self.url, ping_interval=None) as ws:
            logger.info(f"bybit ws connected ({self.url})")
            await self._subscribe(ws)
            ping_task = asyncio.create_task(self._app_ping(ws))
            try:
                async for raw in ws:
                    msg = orjson.loads(raw)
                    async for trade in self._handle_message(msg):
                        yield trade
            finally:
                ping_task.cancel()

    async def _subscribe(self, ws: websockets.WebSocketClientProtocol) -> None:
        args = [f"publicTrade.{s}" for s in self.symbols]
        payload = {"op": "subscribe", "args": args, "req_id": str(uuid.uuid4())}
        await ws.send(orjson.dumps(payload).decode())
        logger.info(f"bybit subscribed: {args}")

    async def _app_ping(self, ws: websockets.WebSocketClientProtocol) -> None:
        while True:
            await asyncio.sleep(PING_INTERVAL_S)
            try:
                await ws.send('{"op":"ping"}')
            except websockets.ConnectionClosed:
                return

    async def _handle_message(self, msg: dict) -> AsyncIterator[TradePrint]:
        # Subscription acks + pings produce {op:..., success:...} envelopes
        if "topic" not in msg:
            if msg.get("op") in ("subscribe", "pong", "ping"):
                return
            logger.debug(f"bybit non-data msg: {msg}")
            return

        topic: str = msg["topic"]
        if not topic.startswith("publicTrade."):
            return

        symbol = topic.split(".", 1)[1]
        for d in msg.get("data", []):
            # https://bybit-exchange.github.io/docs/v5/websocket/public/trade
            # d.T = trade ts millis; d.s = symbol; d.S = side; d.p = price; d.v = size; d.i = id
            try:
                trade = TradePrint(
                    exchange="bybit-testnet" if self.testnet else "bybit",
                    exchange_trade_id=str(d["i"]),
                    symbol=symbol,
                    trade_ts=datetime.fromtimestamp(int(d["T"]) / 1000.0, tz=UTC),
                    side="buy" if d["S"].lower() == "buy" else "sell",
                    price=Decimal(str(d["p"])),
                    size=Decimal(str(d["v"])),
                )
            except (KeyError, ValueError) as e:
                logger.warning(f"bybit malformed trade: {e}; raw={d}")
                continue
            yield trade

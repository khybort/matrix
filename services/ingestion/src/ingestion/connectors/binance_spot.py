"""Binance spot price feed for carry-watchlist spot legs Bybit does not list.

Most negative-funding coins with a borrow rate are lendable only on Binance
cross margin, and many have no Bybit spot pair at all (OGN, RLC, SKL, ORCA,
API3... on 2026-10-09). The hedge is priced where it would be shorted, so
those legs stream from Binance: `<pair>@miniTicker` (last price, throttled),
`<pair>@kline_1m` (closed candles) and `<pair>@depth20@1000ms` (top-20 book,
throttled), live (un)subscribed like the Bybit connector. The combined-stream
endpoint is used because a partial-depth payload does not name its symbol.
Public market data only — no keys, nothing that can place an order.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Iterable
from datetime import UTC, datetime
from decimal import Decimal

import orjson
import websockets
from loguru import logger

from ingestion.connectors.bybit import (
    BarEvent,
    MarketEvent,
    OrderBookEvent,
    TickerEvent,
    chunks,
    is_stale,
)

URL = "wss://stream.binance.com:9443/stream"
EXCHANGE = "binance-spot"
# Both streams push every 1-2 s on a listed pair, and Binance pings every
# 20 s; a minute of silence is a dead socket.
STALE_AFTER_S = 60.0
TICKER_PERSIST_INTERVAL_S = 15.0
BOOK_PERSIST_INTERVAL_S = 10.0


def streams_for(symbols: Iterable[str]) -> list[str]:
    return [f"{s.lower()}@{k}" for s in symbols for k in ("miniTicker", "kline_1m", "depth20@1000ms")]


def parse(
    msg: dict, exchange: str = EXCHANGE, stream: str = ""
) -> TickerEvent | BarEvent | OrderBookEvent | None:
    """One stream payload (`data` of a combined-stream frame) -> event; open
    (unclosed) candles -> None. A depth payload's symbol comes from `stream`."""
    if "lastUpdateId" in msg and "bids" in msg:
        return OrderBookEvent(
            exchange=exchange,
            symbol=stream.split("@", 1)[0].upper(),
            snapshot_ts=datetime.now(UTC),
            bids=[(p, q) for p, q in msg["bids"]],
            asks=[(p, q) for p, q in msg["asks"]],
        )
    e = msg.get("e")
    if e == "24hrMiniTicker":
        return TickerEvent(
            exchange=exchange,
            symbol=msg["s"],
            snapshot_ts=datetime.fromtimestamp(int(msg["E"]) / 1000.0, tz=UTC),
            last_price=Decimal(msg["c"]),
            volume_24h=Decimal(msg["v"]),
            turnover_24h=Decimal(msg["q"]),
        )
    if e == "kline":
        k = msg["k"]
        if not k.get("x"):
            return None
        return BarEvent(
            exchange=exchange,
            symbol=msg["s"],
            ts=datetime.fromtimestamp(int(k["t"]) / 1000.0, tz=UTC),
            open=Decimal(k["o"]),
            high=Decimal(k["h"]),
            low=Decimal(k["l"]),
            close=Decimal(k["c"]),
            volume=Decimal(k["v"]),
        )
    return None


class BinanceSpotConnector:
    def __init__(self, symbols: Iterable[str] = ()) -> None:
        self.symbols: list[str] = list(dict.fromkeys(symbols))
        self._set = set(self.symbols)
        self._ws: websockets.ClientConnection | None = None
        self._has_symbols = asyncio.Event()
        if self.symbols:
            self._has_symbols.set()
        self._last_emit: dict[tuple[str, str], float] = {}
        self._req = 0

    async def _send(self, ws: websockets.ClientConnection, method: str, streams: list[str]) -> None:
        for part in chunks(streams, 100):
            self._req += 1
            await ws.send(orjson.dumps({"method": method, "params": part, "id": self._req}).decode())

    async def set_symbols(self, symbols: Iterable[str]) -> tuple[list[str], list[str]]:
        new = list(dict.fromkeys(symbols))
        added = [s for s in new if s not in set(self.symbols)]
        removed = [s for s in self.symbols if s not in set(new)]
        if not added and not removed:
            return [], []
        self.symbols = new
        self._set = set(new)
        if new:
            self._has_symbols.set()
        else:
            self._has_symbols.clear()
        ws = self._ws
        if ws is not None:
            try:
                if removed:
                    await self._send(ws, "UNSUBSCRIBE", streams_for(removed))
                if added:
                    await self._send(ws, "SUBSCRIBE", streams_for(added))
            except websockets.ConnectionClosed:
                pass
        logger.info(f"binance spot: +{added} -{removed} ({len(new)} symbols)")
        return added, removed

    async def stream(self) -> AsyncIterator[MarketEvent]:
        backoff = 1.0
        while True:
            await self._has_symbols.wait()
            try:
                async for ev in self._run_session():
                    backoff = 1.0
                    yield ev
            except (websockets.ConnectionClosed, OSError, websockets.InvalidStatus) as e:
                logger.warning(f"binance spot ws disconnect: {e}; retry in {backoff:.1f}s")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    async def _run_session(self) -> AsyncIterator[MarketEvent]:
        # websockets answers Binance's server pings itself; we only watch for silence.
        async with websockets.connect(URL, ping_interval=None, max_queue=4096) as ws:
            self._ws = ws
            try:
                await self._send(ws, "SUBSCRIBE", streams_for(self.symbols))
                logger.info(f"binance spot ws connected: {len(self.symbols)} symbols")
                while True:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=STALE_AFTER_S)
                    except TimeoutError:
                        logger.warning(f"binance spot ws: no frames for {STALE_AFTER_S:.0f}s; reconnecting")
                        return
                    frame = orjson.loads(raw)
                    if "result" in frame and "id" in frame:
                        if frame["result"] is not None:
                            logger.warning(f"binance spot ws reply: {frame}")
                        continue
                    stream = frame.get("stream", "")
                    if stream.split("@", 1)[0].upper() not in self._set:
                        continue  # in flight when the pair was unsubscribed
                    try:
                        ev = parse(frame.get("data") or {}, stream=stream)
                    except (KeyError, ValueError, ArithmeticError) as e:
                        logger.warning(f"binance spot malformed {stream}: {e}")
                        continue
                    if ev is None:
                        continue
                    every = {TickerEvent: TICKER_PERSIST_INTERVAL_S, OrderBookEvent: BOOK_PERSIST_INTERVAL_S}.get(type(ev))
                    if every is not None:
                        key, now = (type(ev).__name__, ev.symbol), time.monotonic()
                        if not is_stale(self._last_emit.get(key, 0.0), now, every):
                            continue
                        self._last_emit[key] = now
                    yield ev
            finally:
                self._ws = None

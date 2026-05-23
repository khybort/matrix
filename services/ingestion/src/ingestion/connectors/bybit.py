"""Bybit V5 public WebSocket connector.

Subscribes to linear (USDT-perp) public streams. Testnet by default.
Emits a unified MarketEvent union: TradePrint | OrderBookEvent | TickerEvent.

Internally uses two producers feeding a shared queue:
  - WS reader: handles incoming messages, maintains in-memory book + ticker state,
    emits TradePrint and TickerEvent directly (throttled where needed).
  - Periodic book ticker: every N seconds, emits OrderBookEvent for each symbol
    using the current in-memory book — independent of delta frequency.

Topics:
    publicTrade.<SYMBOL>       — trade prints
    orderbook.50.<SYMBOL>      — top-50 book, snapshot + deltas
    tickers.<SYMBOL>           — last/mark/index/funding/OI/volume snapshots
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import orjson
import websockets
from loguru import logger

PING_INTERVAL_S = 20

# How often we emit a top-of-book snapshot per symbol, regardless of delta frequency.
ORDERBOOK_PERSIST_INTERVAL_S = 2.0
ORDERBOOK_PERSIST_DEPTH = 25

# Throttle ticker emissions. Funding-rate changes always emit, ignoring throttle.
TICKER_PERSIST_INTERVAL_S = 5.0


@dataclass(slots=True)
class TradePrint:
    exchange: str
    exchange_trade_id: str
    symbol: str
    trade_ts: datetime
    side: str
    price: Decimal
    size: Decimal


@dataclass(slots=True)
class OrderBookEvent:
    exchange: str
    symbol: str
    snapshot_ts: datetime
    bids: list[tuple[str, str]]
    asks: list[tuple[str, str]]


@dataclass(slots=True)
class TickerEvent:
    exchange: str
    symbol: str
    snapshot_ts: datetime
    last_price: Decimal | None = None
    mark_price: Decimal | None = None
    index_price: Decimal | None = None
    funding_rate: Decimal | None = None
    next_funding_ts: datetime | None = None
    open_interest: Decimal | None = None
    volume_24h: Decimal | None = None
    turnover_24h: Decimal | None = None


MarketEvent = TradePrint | OrderBookEvent | TickerEvent


@dataclass(slots=True)
class _BookState:
    bids: dict[str, str] = field(default_factory=dict)
    asks: dict[str, str] = field(default_factory=dict)
    has_snapshot: bool = False

    def apply(self, type_: str, b: list[list[str]], a: list[list[str]]) -> None:
        if type_ == "snapshot":
            self.bids = {p: s for p, s in b}
            self.asks = {p: s for p, s in a}
            self.has_snapshot = True
            return
        for p, s in b:
            if Decimal(s) == 0:
                self.bids.pop(p, None)
            else:
                self.bids[p] = s
        for p, s in a:
            if Decimal(s) == 0:
                self.asks.pop(p, None)
            else:
                self.asks[p] = s

    def top(self, depth: int) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
        bids_sorted = sorted(self.bids.items(), key=lambda kv: -Decimal(kv[0]))[:depth]
        asks_sorted = sorted(self.asks.items(), key=lambda kv: Decimal(kv[0]))[:depth]
        return bids_sorted, asks_sorted


@dataclass(slots=True)
class _TickerCarry:
    last_persist: float = 0.0
    last_funding_rate: str | None = None
    state: dict[str, str] = field(default_factory=dict)


class BybitConnector:
    """Streams trade prints, orderbook, and ticker events from Bybit V5 WS."""

    def __init__(self, symbols: Iterable[str], *, testnet: bool = True) -> None:
        self.symbols = list(symbols)
        self.testnet = testnet
        self.url = (
            "wss://stream-testnet.bybit.com/v5/public/linear"
            if testnet
            else "wss://stream.bybit.com/v5/public/linear"
        )
        self._exchange = "bybit-testnet" if testnet else "bybit"
        self._books: dict[str, _BookState] = {s: _BookState() for s in self.symbols}
        self._tickers: dict[str, _TickerCarry] = {s: _TickerCarry() for s in self.symbols}

    async def stream(self) -> AsyncIterator[MarketEvent]:
        """Yield MarketEvents forever; reconnects on disconnect with backoff."""
        backoff = 1.0
        while True:
            try:
                async for event in self._run_session():
                    backoff = 1.0
                    yield event
            except (websockets.ConnectionClosed, OSError) as e:
                logger.warning(f"bybit ws disconnect: {e}; retry in {backoff:.1f}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _run_session(self) -> AsyncIterator[MarketEvent]:
        queue: asyncio.Queue[MarketEvent | None] = asyncio.Queue(maxsize=10000)

        async with websockets.connect(self.url, ping_interval=None) as ws:
            logger.info(f"bybit ws connected ({self.url})")
            await self._subscribe(ws)

            tasks = [
                asyncio.create_task(self._reader(ws, queue), name="bybit-reader"),
                asyncio.create_task(self._book_ticker(queue), name="bybit-book-ticker"),
                asyncio.create_task(self._app_ping(ws), name="bybit-ping"),
            ]
            try:
                while True:
                    event = await queue.get()
                    if event is None:  # reader signaled disconnect
                        return
                    yield event
            finally:
                for t in tasks:
                    t.cancel()
                # let cancellation propagate cleanly
                await asyncio.gather(*tasks, return_exceptions=True)

    async def _subscribe(self, ws: websockets.WebSocketClientProtocol) -> None:
        args: list[str] = []
        for s in self.symbols:
            args.append(f"publicTrade.{s}")
            args.append(f"orderbook.50.{s}")
            args.append(f"tickers.{s}")
        payload = {"op": "subscribe", "args": args, "req_id": str(uuid.uuid4())}
        await ws.send(orjson.dumps(payload).decode())
        logger.info(f"bybit subscribed: {len(args)} topics across {len(self.symbols)} symbols")

    async def _app_ping(self, ws: websockets.WebSocketClientProtocol) -> None:
        while True:
            await asyncio.sleep(PING_INTERVAL_S)
            try:
                await ws.send('{"op":"ping"}')
            except websockets.ConnectionClosed:
                return

    async def _reader(
        self,
        ws: websockets.WebSocketClientProtocol,
        queue: asyncio.Queue[MarketEvent | None],
    ) -> None:
        try:
            async for raw in ws:
                msg = orjson.loads(raw)
                for ev in self._handle_message(msg):
                    await queue.put(ev)
        except websockets.ConnectionClosed:
            await queue.put(None)

    async def _book_ticker(self, queue: asyncio.Queue[MarketEvent | None]) -> None:
        """Periodically emit current book state for each symbol."""
        while True:
            await asyncio.sleep(ORDERBOOK_PERSIST_INTERVAL_S)
            ts = datetime.now(UTC)
            for symbol, book in self._books.items():
                if not book.has_snapshot:
                    continue
                bids, asks = book.top(ORDERBOOK_PERSIST_DEPTH)
                if not bids or not asks:
                    continue
                await queue.put(
                    OrderBookEvent(
                        exchange=self._exchange,
                        symbol=symbol,
                        snapshot_ts=ts,
                        bids=list(bids),
                        asks=list(asks),
                    )
                )

    def _handle_message(self, msg: dict) -> list[MarketEvent]:
        if "topic" not in msg:
            if msg.get("op") in ("subscribe", "pong", "ping"):
                return []
            logger.debug(f"bybit non-data msg: {msg}")
            return []

        topic: str = msg["topic"]

        if topic.startswith("publicTrade."):
            return list(self._parse_trades(topic, msg))

        if topic.startswith("orderbook.50."):
            self._apply_orderbook(topic, msg)
            return []  # persistence handled by _book_ticker

        if topic.startswith("tickers."):
            ev = self._handle_ticker(topic, msg)
            return [ev] if ev is not None else []

        return []

    def _parse_trades(self, topic: str, msg: dict) -> Iterable[TradePrint]:
        symbol = topic.split(".", 1)[1]
        for d in msg.get("data", []):
            try:
                yield TradePrint(
                    exchange=self._exchange,
                    exchange_trade_id=str(d["i"]),
                    symbol=symbol,
                    trade_ts=datetime.fromtimestamp(int(d["T"]) / 1000.0, tz=UTC),
                    side="buy" if d["S"].lower() == "buy" else "sell",
                    price=Decimal(str(d["p"])),
                    size=Decimal(str(d["v"])),
                )
            except (KeyError, ValueError) as e:
                logger.warning(f"bybit malformed trade: {e}; raw={d}")

    def _apply_orderbook(self, topic: str, msg: dict) -> None:
        symbol = topic.split(".", 2)[2]
        book = self._books.setdefault(symbol, _BookState())
        data = msg.get("data") or {}
        type_ = msg.get("type", "delta")
        b = data.get("b") or []
        a = data.get("a") or []
        try:
            book.apply(type_, b, a)
        except (ValueError, KeyError) as e:
            logger.warning(f"bybit ob apply error sym={symbol}: {e}")

    def _handle_ticker(self, topic: str, msg: dict) -> TickerEvent | None:
        symbol = topic.split(".", 1)[1]
        carry = self._tickers.setdefault(symbol, _TickerCarry())
        data = msg.get("data") or {}
        for k, v in data.items():
            carry.state[k] = v

        now = time.monotonic()
        funding_now = data.get("fundingRate")
        funding_changed = (
            funding_now is not None and funding_now != carry.last_funding_rate
        )
        time_elapsed = (now - carry.last_persist) >= TICKER_PERSIST_INTERVAL_S
        if not (funding_changed or time_elapsed):
            return None

        carry.last_persist = now
        if funding_now is not None:
            carry.last_funding_rate = funding_now

        return self._build_ticker_event(symbol, msg, carry.state)

    def _build_ticker_event(
        self, symbol: str, msg: dict, state: dict[str, Any]
    ) -> TickerEvent | None:
        def dec(field_name: str) -> Decimal | None:
            v = state.get(field_name)
            if v in (None, ""):
                return None
            try:
                return Decimal(str(v))
            except (ValueError, ArithmeticError):
                return None

        def opt_ts_ms(field_name: str) -> datetime | None:
            v = state.get(field_name)
            if v in (None, "", "0"):
                return None
            try:
                return datetime.fromtimestamp(int(v) / 1000.0, tz=UTC)
            except (ValueError, OverflowError):
                return None

        ts_ms = msg.get("ts")
        snapshot_ts = (
            datetime.fromtimestamp(int(ts_ms) / 1000.0, tz=UTC)
            if ts_ms is not None
            else datetime.now(UTC)
        )

        return TickerEvent(
            exchange=self._exchange,
            symbol=symbol,
            snapshot_ts=snapshot_ts,
            last_price=dec("lastPrice"),
            mark_price=dec("markPrice"),
            index_price=dec("indexPrice"),
            funding_rate=dec("fundingRate"),
            next_funding_ts=opt_ts_ms("nextFundingTime"),
            open_interest=dec("openInterest"),
            volume_24h=dec("volume24h"),
            turnover_24h=dec("turnover24h"),
        )

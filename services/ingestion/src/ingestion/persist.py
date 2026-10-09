"""Batch-write market events to Postgres, routed by event type.

Several streams (perp, Bybit spot, Binance spot) each run their own
`persist_events` loop; bars from the spot legs upsert into market_bars.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

from loguru import logger
from sqlalchemy.dialects.postgresql import insert as pg_insert

from matrix_shared import session_scope
from matrix_shared.models import MarketBar, MarketTrade, OrderBookSnapshot, TickerSnapshot

from ingestion.connectors.bybit import (
    BarEvent,
    MarketEvent,
    OrderBookEvent,
    TickerEvent,
    TradePrint,
)

TRADE_BATCH_SIZE = 100
SNAPSHOT_BATCH_SIZE = 25  # orderbook + ticker are bigger rows; smaller batches
FLUSH_INTERVAL_S = 2.0


class _Batches:
    __slots__ = ("bars", "orderbooks", "tickers", "trades")

    def __init__(self) -> None:
        self.trades: list[TradePrint] = []
        self.orderbooks: list[OrderBookEvent] = []
        self.tickers: list[TickerEvent] = []
        self.bars: list[BarEvent] = []

    def total(self) -> int:
        return len(self.trades) + len(self.orderbooks) + len(self.tickers) + len(self.bars)


async def persist_events(stream: AsyncIterator[MarketEvent]) -> None:
    batches = _Batches()
    last_flush = asyncio.get_event_loop().time()

    async def flush() -> None:
        nonlocal last_flush
        if batches.total() == 0:
            return

        async with session_scope() as session:
            if batches.trades:
                stmt = pg_insert(MarketTrade).values(
                    [
                        {
                            "exchange": t.exchange,
                            "exchange_trade_id": t.exchange_trade_id,
                            "symbol": t.symbol,
                            "trade_ts": t.trade_ts,
                            "side": t.side,
                            "price": t.price,
                            "size": t.size,
                        }
                        for t in batches.trades
                    ]
                )
                stmt = stmt.on_conflict_do_nothing(
                    index_elements=["exchange", "exchange_trade_id"]
                )
                await session.execute(stmt)

            if batches.orderbooks:
                await session.execute(
                    pg_insert(OrderBookSnapshot).values(
                        [
                            {
                                "exchange": o.exchange,
                                "symbol": o.symbol,
                                "snapshot_ts": o.snapshot_ts,
                                "bids": o.bids,
                                "asks": o.asks,
                            }
                            for o in batches.orderbooks
                        ]
                    )
                )

            if batches.tickers:
                await session.execute(
                    pg_insert(TickerSnapshot).values(
                        [
                            {
                                "exchange": t.exchange,
                                "symbol": t.symbol,
                                "snapshot_ts": t.snapshot_ts,
                                "last_price": t.last_price,
                                "mark_price": t.mark_price,
                                "index_price": t.index_price,
                                "funding_rate": t.funding_rate,
                                "next_funding_ts": t.next_funding_ts,
                                "open_interest": t.open_interest,
                                "volume_24h": t.volume_24h,
                                "turnover_24h": t.turnover_24h,
                            }
                            for t in batches.tickers
                        ]
                    )
                )

            if batches.bars:
                # One row per key: a repeated push in the same batch would make
                # ON CONFLICT DO UPDATE touch a row twice and fail the flush.
                latest = {(b.asset_class, b.symbol, b.ts): b for b in batches.bars}
                stmt = pg_insert(MarketBar).values(
                    [
                        {
                            "id": uuid.uuid4(),
                            "symbol": b.symbol,
                            "asset_class": b.asset_class,
                            "interval": "1m",
                            "ts": b.ts,
                            "open": b.open,
                            "high": b.high,
                            "low": b.low,
                            "close": b.close,
                            "volume": b.volume,
                            "source": b.exchange,
                        }
                        for b in latest.values()
                    ]
                )
                stmt = stmt.on_conflict_do_update(
                    constraint="uq_market_bars_class_sit",
                    set_={c: stmt.excluded[c] for c in ("open", "high", "low", "close", "volume", "source")},
                )
                await session.execute(stmt)

        logger.info(
            f"persisted trades={len(batches.trades)} "
            f"ob={len(batches.orderbooks)} ticker={len(batches.tickers)}"
            + (f" bars={len(batches.bars)}" if batches.bars else "")
        )
        batches.bars.clear()
        batches.trades.clear()
        batches.orderbooks.clear()
        batches.tickers.clear()
        last_flush = asyncio.get_event_loop().time()

    async for event in stream:
        if isinstance(event, TradePrint):
            batches.trades.append(event)
        elif isinstance(event, OrderBookEvent):
            batches.orderbooks.append(event)
        elif isinstance(event, TickerEvent):
            batches.tickers.append(event)
        elif isinstance(event, BarEvent):
            batches.bars.append(event)
        else:
            logger.warning(f"unknown event type: {type(event).__name__}")
            continue

        now = asyncio.get_event_loop().time()
        should_flush = (
            len(batches.trades) >= TRADE_BATCH_SIZE
            or len(batches.orderbooks) >= SNAPSHOT_BATCH_SIZE
            or len(batches.tickers) >= SNAPSHOT_BATCH_SIZE
            or (now - last_flush) >= FLUSH_INTERVAL_S
        )
        if should_flush:
            await flush()

    await flush()

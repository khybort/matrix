"""Batch-write market trades to Postgres."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from loguru import logger
from sqlalchemy.dialects.postgresql import insert as pg_insert

from matrix_shared import session_scope
from matrix_shared.models import MarketTrade

from ingestion.connectors.bybit import TradePrint

BATCH_SIZE = 100
FLUSH_INTERVAL_S = 2.0


async def persist_trades(stream: AsyncIterator[TradePrint]) -> None:
    """Drain a TradePrint stream, batching inserts to keep DB load low.

    Uses ON CONFLICT DO NOTHING on (exchange, exchange_trade_id) so reconnect
    replays don't blow up.
    """
    batch: list[TradePrint] = []
    last_flush = asyncio.get_event_loop().time()

    async def flush() -> None:
        nonlocal last_flush
        if not batch:
            return
        rows = [
            {
                "exchange": t.exchange,
                "exchange_trade_id": t.exchange_trade_id,
                "symbol": t.symbol,
                "trade_ts": t.trade_ts,
                "side": t.side,
                "price": t.price,
                "size": t.size,
            }
            for t in batch
        ]
        async with session_scope() as session:
            stmt = pg_insert(MarketTrade).values(rows)
            stmt = stmt.on_conflict_do_nothing(index_elements=["exchange", "exchange_trade_id"])
            await session.execute(stmt)
        logger.info(f"persisted {len(batch)} trades")
        batch.clear()
        last_flush = asyncio.get_event_loop().time()

    async for trade in stream:
        batch.append(trade)
        now = asyncio.get_event_loop().time()
        if len(batch) >= BATCH_SIZE or (now - last_flush) >= FLUSH_INTERVAL_S:
            await flush()

    await flush()

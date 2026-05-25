"""market_trades → market_bars OHLCV aggregator.

Phase 2/3 bridge. Strategy/backtest layers consume bars, not raw ticks.
Pushes the bucketing work to Postgres (date_trunc + GROUP BY + UPSERT)
because the SELECT scan is fast with ix_market_trades_symbol_ts and
moving 718k rows through Python would be wasteful.

Usage:
    uv run python -m ingestion.bars                       # default: 60s loop, 1m bars
    uv run python -m ingestion.bars --interval 30
    uv run python -m ingestion.bars --once                # one tick, exit
    uv run python -m ingestion.bars --backfill-all        # rebuild every bar from
                                                          # earliest trade to NOW, then exit
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import text

from matrix_shared import session_scope

DEFAULT_INTERVAL_S = 60.0
DEFAULT_LOOKBACK_MINUTES = 5  # how far back each tick re-aggregates


# Postgres does the bucketing. ON CONFLICT DO UPDATE makes the operation
# idempotent: re-running over the same window produces the same bar row
# (open/close stay stable, late-arriving trades fix any incomplete bar).
_AGGREGATE_SQL = text("""
INSERT INTO market_bars
  (id, symbol, asset_class, interval, ts, open, high, low, close, volume, source, created_at)
SELECT
  gen_random_uuid(),
  symbol,
  'crypto'                                  AS asset_class,
  '1m'                                      AS interval,
  date_trunc('minute', trade_ts)            AS ts,
  (array_agg(price ORDER BY trade_ts ASC, exchange_trade_id ASC))[1]   AS open,
  MAX(price)                                AS high,
  MIN(price)                                AS low,
  (array_agg(price ORDER BY trade_ts DESC, exchange_trade_id DESC))[1] AS close,
  SUM(size)                                 AS volume,
  exchange                                  AS source,
  NOW()                                     AS created_at
FROM market_trades
WHERE trade_ts >= :since
  AND trade_ts <  :until
GROUP BY symbol, exchange, date_trunc('minute', trade_ts)
ON CONFLICT (symbol, interval, ts) DO UPDATE
  SET open   = EXCLUDED.open,
      high   = EXCLUDED.high,
      low    = EXCLUDED.low,
      close  = EXCLUDED.close,
      volume = EXCLUDED.volume,
      source = EXCLUDED.source
""")


async def aggregate_window(since: datetime, until: datetime) -> int:
    """Aggregate trades in [since, until) into 1m bars. Returns rows affected.

    `until` is exclusive so consecutive ticks don't double-count the boundary
    minute. Re-running over the same window is idempotent (upsert).
    """
    async with session_scope() as session:
        result = await session.execute(_AGGREGATE_SQL, {"since": since, "until": until})
        return result.rowcount or 0


async def _earliest_trade_ts() -> datetime | None:
    async with session_scope() as session:
        row = await session.execute(text("SELECT MIN(trade_ts) FROM market_trades"))
        v = row.scalar_one_or_none()
        return v


async def backfill_all() -> int:
    """Aggregate every bar from the earliest trade to NOW. One-shot."""
    earliest = await _earliest_trade_ts()
    if earliest is None:
        logger.info("backfill: no trades, nothing to do")
        return 0
    until = datetime.now(timezone.utc)
    logger.info(f"backfill: aggregating {earliest.isoformat()} → {until.isoformat()}")
    n = await aggregate_window(earliest, until)
    logger.info(f"backfill: wrote/updated {n} bar rows")
    return n


async def tick(lookback_minutes: int) -> int:
    """Re-aggregate the last `lookback_minutes` worth of bars. Idempotent."""
    until = datetime.now(timezone.utc)
    since = until - timedelta(minutes=lookback_minutes)
    n = await aggregate_window(since, until)
    if n:
        logger.info(f"tick: upserted {n} bar rows in last {lookback_minutes}m")
    return n


async def run(interval_s: float, lookback_minutes: int) -> None:
    stop = asyncio.Event()

    def _handle_signal(*_: object) -> None:
        logger.info("shutdown signal received")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal)

    # One-shot backfill on startup so the table is current before the loop
    # settles into incremental mode. Idempotent; safe to repeat.
    try:
        await backfill_all()
    except Exception as e:
        logger.exception(f"startup backfill failed: {e}")

    while not stop.is_set():
        try:
            await tick(lookback_minutes)
        except Exception as e:
            logger.exception(f"tick failed: {e}")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
        except TimeoutError:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Matrix market_trades → market_bars aggregator")
    parser.add_argument(
        "--interval", type=float, default=DEFAULT_INTERVAL_S,
        help=f"Tick interval seconds (default {DEFAULT_INTERVAL_S})",
    )
    parser.add_argument(
        "--lookback-minutes", type=int, default=DEFAULT_LOOKBACK_MINUTES,
        help=f"How many minutes back to re-aggregate each tick (default {DEFAULT_LOOKBACK_MINUTES})",
    )
    parser.add_argument("--once", action="store_true", help="Run one tick and exit")
    parser.add_argument("--backfill-all", action="store_true",
                        help="Rebuild every bar from earliest trade to now, then exit")
    args = parser.parse_args()

    logger.remove()
    logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level: <5} | {message}")
    logger.info(
        f"bars start: interval={args.interval}s lookback={args.lookback_minutes}m "
        f"once={args.once} backfill_all={args.backfill_all}"
    )

    if args.backfill_all:
        asyncio.run(backfill_all())
    elif args.once:
        asyncio.run(tick(args.lookback_minutes))
    else:
        asyncio.run(run(args.interval, args.lookback_minutes))


if __name__ == "__main__":
    main()

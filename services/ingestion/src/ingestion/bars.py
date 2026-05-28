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
import os
import signal
import sys
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
from loguru import logger
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from matrix_shared.markets.crypto import crypto_universe

from matrix_shared import session_scope
from matrix_shared.models import MarketBar

DEFAULT_INTERVAL_S = 60.0
DEFAULT_LOOKBACK_MINUTES = 5  # how far back each tick re-aggregates

# Bybit REST kline (mainnet). Testnet kline data is sparse, mainnet is reliable
# and matches the websocket connector's --mainnet default.
BYBIT_KLINE_URL = "https://api.bybit.com/v5/market/kline"
BYBIT_KLINE_MAX = 1000  # API limit per request

# Default REST backfill window: anything missing within the past 7 days.
# Bybit retains 1m klines well beyond this; we cap to keep startup cheap.
DEFAULT_REST_BACKFILL_HOURS = 168.0


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
ON CONFLICT (asset_class, symbol, interval, ts) DO UPDATE
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


async def _fetch_bybit_klines(
    client: httpx.AsyncClient, symbol: str, start_ms: int, end_ms: int
) -> list[list[str]]:
    """One call to Bybit /v5/market/kline. Returns list of kline rows.

    Bybit returns klines in DESCENDING ts order, [start_ms, open, high, low,
    close, volume, turnover]. We don't reverse here — caller upserts and order
    doesn't matter.
    """
    r = await client.get(
        BYBIT_KLINE_URL,
        params={
            "category": "linear",
            "symbol": symbol,
            "interval": "1",  # 1-minute
            "start": start_ms,
            "end": end_ms,
            "limit": BYBIT_KLINE_MAX,
        },
        timeout=15.0,
    )
    r.raise_for_status()
    body = r.json()
    if body.get("retCode") != 0:
        raise RuntimeError(f"bybit kline error: {body}")
    return body.get("result", {}).get("list", [])


async def rest_backfill_symbol(
    client: httpx.AsyncClient, symbol: str, since: datetime, until: datetime
) -> int:
    """Page through Bybit kline API for one symbol, UPSERT bars. Returns count."""
    total = 0
    cursor_end = until
    while cursor_end > since:
        start_ms = int(since.timestamp() * 1000)
        end_ms = int(cursor_end.timestamp() * 1000)
        try:
            rows = await _fetch_bybit_klines(client, symbol, start_ms, end_ms)
        except (httpx.HTTPError, RuntimeError) as e:
            logger.warning(f"rest backfill {symbol}: fetch failed ({e}); aborting")
            break
        if not rows:
            break

        # Build upsert payload. Bybit kline ts is the bar's open time (UTC ms).
        # source='bybit-kline' marks the provenance; later trade-derived bars
        # on the same minute will overwrite via ON CONFLICT DO UPDATE — the
        # tick-stream version has finer granularity when we have it.
        values = []
        oldest_ms = end_ms
        for row in rows:
            ts_ms = int(row[0])
            oldest_ms = min(oldest_ms, ts_ms)
            values.append({
                "id": uuid.uuid4(),
                "symbol": symbol,
                "asset_class": "crypto",
                "interval": "1m",
                "ts": datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc),
                "open": Decimal(row[1]),
                "high": Decimal(row[2]),
                "low": Decimal(row[3]),
                "close": Decimal(row[4]),
                "volume": Decimal(row[5]),
                "source": "bybit-kline",
                "created_at": datetime.now(timezone.utc),
            })

        async with session_scope() as session:
            stmt = pg_insert(MarketBar).values(values)
            # Kline data fills holes only — never overwrite a trade-derived bar
            # (those have tick-level fidelity; kline is the 1m candle Bybit
            # publishes from its own aggregation, slightly less precise).
            stmt = stmt.on_conflict_do_nothing(
                constraint="uq_market_bars_class_sit"
            )
            await session.execute(stmt)
        total += len(values)

        # Advance cursor to just before the oldest ts we just fetched.
        # If we got fewer than the API max, we've reached the start.
        if len(rows) < BYBIT_KLINE_MAX:
            break
        cursor_end = datetime.fromtimestamp(oldest_ms / 1000.0, tz=timezone.utc) - timedelta(seconds=1)

    return total


async def rest_backfill_missing(symbols: list[str], lookback_hours: float) -> int:
    """For each symbol, REST-backfill bars across the FULL window (now-lookback → now).

    The connector can be down for arbitrary stretches, so we can't just forward-
    fill from MAX(ts) — there are internal gaps too. ON CONFLICT DO NOTHING
    makes the operation a hole-filler: trade-derived bars stay; missing minutes
    get kline-derived bars. Bybit's kline data is the floor of coverage.
    """
    until = datetime.now(timezone.utc)
    since = until - timedelta(hours=lookback_hours)
    total = 0
    async with httpx.AsyncClient() as client:
        for symbol in symbols:
            logger.info(
                f"rest backfill {symbol}: {since.isoformat()} → {until.isoformat()}"
            )
            n = await rest_backfill_symbol(client, symbol, since, until)
            logger.info(f"rest backfill {symbol}: fetched {n} candle rows (holes filled)")
            total += n
    return total


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


async def run(
    interval_s: float,
    lookback_minutes: int,
    *,
    rest_backfill_symbols: list[str],
    rest_backfill_hours: float,
) -> None:
    stop = asyncio.Event()

    def _handle_signal(*_: object) -> None:
        logger.info("shutdown signal received")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal)

    # Recover missed bars during container downtime. Runs FIRST so trade-based
    # aggregation (next) can overwrite kline-derived bars with higher-fidelity
    # values for any minutes the connector did cover.
    if rest_backfill_symbols:
        try:
            await rest_backfill_missing(rest_backfill_symbols, rest_backfill_hours)
        except Exception as e:
            logger.exception(f"startup REST backfill failed: {e}")

    # One-shot trade-aggregation backfill so the table is current before the
    # loop settles into incremental mode. Idempotent; safe to repeat.
    try:
        await backfill_all()
    except Exception as e:
        logger.exception(f"startup trade backfill failed: {e}")

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
    _env_backfill = os.environ.get("BARS_REST_BACKFILL_SYMBOLS", "").strip()
    parser.add_argument(
        "--rest-backfill-symbols",
        nargs="*",
        default=[s for s in _env_backfill.split(",") if s] if _env_backfill else crypto_universe(),
        help="Symbols to fetch from Bybit kline REST on startup. "
             "Default is the full crypto_universe() (overridable via "
             "BARS_REST_BACKFILL_SYMBOLS env or this flag). Empty list disables REST backfill.",
    )
    parser.add_argument(
        "--rest-backfill-hours",
        type=float, default=DEFAULT_REST_BACKFILL_HOURS,
        help=f"How far back to REST-backfill on startup (default {DEFAULT_REST_BACKFILL_HOURS}h)",
    )
    parser.add_argument(
        "--rest-backfill-only", action="store_true",
        help="Run REST backfill once and exit (for ops / one-shot recovery)",
    )
    args = parser.parse_args()
    # nargs='*' with a string default leaves it as the string; coerce explicitly.
    if isinstance(args.rest_backfill_symbols, str):
        args.rest_backfill_symbols = [s for s in args.rest_backfill_symbols.split(",") if s]
    args.rest_backfill_symbols = [s for s in args.rest_backfill_symbols if s]

    logger.remove()
    logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level: <5} | {message}")
    logger.info(
        f"bars start: interval={args.interval}s lookback={args.lookback_minutes}m "
        f"once={args.once} backfill_all={args.backfill_all}"
    )

    if args.rest_backfill_only:
        asyncio.run(
            rest_backfill_missing(args.rest_backfill_symbols, args.rest_backfill_hours)
        )
    elif args.backfill_all:
        asyncio.run(backfill_all())
    elif args.once:
        asyncio.run(tick(args.lookback_minutes))
    else:
        asyncio.run(
            run(
                args.interval,
                args.lookback_minutes,
                rest_backfill_symbols=args.rest_backfill_symbols,
                rest_backfill_hours=args.rest_backfill_hours,
            )
        )


if __name__ == "__main__":
    main()

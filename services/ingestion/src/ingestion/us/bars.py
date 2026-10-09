"""yfinance bar poller for US equities — writes to `market_bars` (asset_class='us').

Mirrors the BIST poller but for US tickers (no exchange suffix) and the NYSE
calendar (`matrix_shared.markets.us_calendar`):
  * During the regular US session: 1m bars every 60s (idempotent upsert by
    (asset_class, symbol, interval, ts)).
  * Outside session: 1d bars hourly + a periodic 1m backfill so intraday
    strategies have history before the next open. yfinance keeps ~7d of 1m.

Failure semantics: a failed batch is logged and skipped; the next cycle
retries. Yahoo silently omits unknown tickers — we tolerate that.
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pandas as pd
import yfinance as yf
from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from matrix_shared import session_scope
from matrix_shared.markets import us_calendar
from matrix_shared.models import MarketBar, UsSymbol

ASSET_CLASS = "us"
BATCH_SIZE = 50
INTRADAY_INTERVAL = "1m"
EOD_INTERVAL = "1d"
INTRADAY_POLL_S = 60
EOD_POLL_S = 3600
INTRADAY_BACKFILL_PERIOD = "5d"
OFF_SESSION_1M_REFRESH_H = 6
UPSERT_CHUNK = 2500  # asyncpg caps bind params at 32767 (~11 cols/row)


def in_session(now: datetime | None = None) -> bool:
    return us_calendar.is_session_open(now)


async def load_active_symbols() -> list[str]:
    async with session_scope() as session:
        rows = await session.execute(
            select(UsSymbol.symbol).where(UsSymbol.active.is_(True))
        )
        return sorted({r[0] for r in rows})


async def intraday_bootstrap_needed(*, max_age: timedelta = timedelta(days=2)) -> bool:
    """True when we have no recent 1m US bars (strategies cannot fire)."""
    async with session_scope() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(MarketBar)
            .where(MarketBar.asset_class == ASSET_CLASS)
            .where(MarketBar.interval == INTRADAY_INTERVAL)
        )
        if not count:
            return True
        latest = await session.scalar(
            select(func.max(MarketBar.ts))
            .where(MarketBar.asset_class == ASSET_CLASS)
            .where(MarketBar.interval == INTRADAY_INTERVAL)
        )
    if latest is None:
        return True
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=UTC)
    return latest < datetime.now(UTC) - max_age


async def backfill_intraday(
    symbols: list[str], *, period: str = INTRADAY_BACKFILL_PERIOD
) -> int:
    if not symbols:
        return 0
    t0 = datetime.now(UTC)
    inserted = await poll_once(symbols, interval=INTRADAY_INTERVAL, period=period)
    dt = (datetime.now(UTC) - t0).total_seconds()
    logger.info(
        f"US 1m backfill: syms={len(symbols)} inserted={inserted} "
        f"period={period} took={dt:.1f}s"
    )
    return inserted


def _batched(items: list[str], n: int) -> list[list[str]]:
    return [items[i : i + n] for i in range(0, len(items), n)]


def _fetch_batch(tickers: list[str], *, interval: str, period: str) -> pd.DataFrame:
    try:
        df = yf.download(
            tickers=" ".join(tickers),
            period=period,
            interval=interval,
            group_by="ticker",
            threads=False,
            progress=False,
            auto_adjust=False,
            prepost=False,
        )
    except Exception as e:
        logger.warning(f"yfinance batch failed ({interval}, {len(tickers)} syms): {e}")
        return pd.DataFrame()
    return df if df is not None else pd.DataFrame()


def _rows_from_batch(df: pd.DataFrame, tickers: list[str], *, interval: str) -> list[dict]:
    if df.empty:
        return []
    rows: list[dict] = []
    now = datetime.now(UTC)
    multi = isinstance(df.columns, pd.MultiIndex)

    for ticker in tickers:
        try:
            tdf = df[ticker] if multi else df
        except KeyError:
            continue
        if tdf is None or tdf.empty:
            continue
        for ts, bar in tdf.dropna().iterrows():
            try:
                open_ = bar["Open"]
                high = bar["High"]
                low = bar["Low"]
                close = bar["Close"]
                volume = bar.get("Volume", 0)
            except (KeyError, IndexError):
                continue
            if pd.isna(open_) or pd.isna(close):
                continue
            ts_dt = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
            if ts_dt.tzinfo is None:
                ts_dt = ts_dt.replace(tzinfo=UTC)
            else:
                ts_dt = ts_dt.astimezone(UTC)
            rows.append(
                {
                    "id": uuid.uuid4(),
                    "symbol": ticker,
                    "asset_class": ASSET_CLASS,
                    "interval": interval,
                    "ts": ts_dt,
                    "open": Decimal(str(open_)),
                    "high": Decimal(str(high)),
                    "low": Decimal(str(low)),
                    "close": Decimal(str(close)),
                    "volume": Decimal(str(volume or 0)),
                    "source": "yfinance",
                    "created_at": now,
                }
            )
    return rows


async def _upsert(rows: list[dict]) -> int:
    if not rows:
        return 0
    total = 0
    async with session_scope() as session:
        for i in range(0, len(rows), UPSERT_CHUNK):
            chunk = rows[i : i + UPSERT_CHUNK]
            stmt = pg_insert(MarketBar).values(chunk)
            stmt = stmt.on_conflict_do_nothing(constraint="uq_market_bars_class_sit")
            result = await session.execute(stmt)
            total += result.rowcount or 0
    return total


async def poll_once(symbols: list[str], *, interval: str, period: str) -> int:
    if not symbols:
        return 0
    total = 0
    for batch in _batched(symbols, BATCH_SIZE):
        df = await asyncio.to_thread(_fetch_batch, batch, interval=interval, period=period)
        rows = _rows_from_batch(df, batch, interval=interval)
        inserted = await _upsert(rows)
        total += inserted
        logger.debug(f"batch {batch[0]}…+{len(batch) - 1}: rows={len(rows)} inserted={inserted}")
    return total


async def run() -> None:
    stop = asyncio.Event()

    def _handle_signal(*_: object) -> None:
        logger.info("shutdown signal received")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal)

    last_eod_pull: datetime | None = None
    last_off_session_1m: datetime | None = None
    while not stop.is_set():
        symbols = await load_active_symbols()
        if not symbols:
            logger.warning(
                "no active US symbols — run `matrix-us-symbols --bootstrap-active` "
                "or wait for the discover pass on ingestion startup"
            )
            await asyncio.sleep(60)
            continue

        if await intraday_bootstrap_needed():
            await backfill_intraday(symbols)
            last_off_session_1m = datetime.now(UTC)

        if in_session():
            t0 = datetime.now(UTC)
            inserted = await poll_once(symbols, interval=INTRADAY_INTERVAL, period="1d")
            dt = (datetime.now(UTC) - t0).total_seconds()
            logger.info(f"intraday poll: syms={len(symbols)} inserted={inserted} took={dt:.1f}s")
            sleep_s = INTRADAY_POLL_S
        else:
            now = datetime.now(UTC)
            stale = last_eod_pull is None or (now - last_eod_pull) > timedelta(hours=1)
            if stale:
                inserted = await poll_once(symbols, interval=EOD_INTERVAL, period="5d")
                last_eod_pull = now
                logger.info(f"EOD poll: syms={len(symbols)} inserted={inserted}")
            stale_1m = (
                last_off_session_1m is None
                or (now - last_off_session_1m) > timedelta(hours=OFF_SESSION_1M_REFRESH_H)
            )
            if stale_1m:
                await backfill_intraday(symbols)
                last_off_session_1m = now
            sleep_s = EOD_POLL_S

        try:
            await asyncio.wait_for(stop.wait(), timeout=sleep_s)
        except TimeoutError:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Matrix US equities bar poller (yfinance)")
    parser.add_argument("--once", action="store_true", help="Poll one cycle and exit (smoketest)")
    parser.add_argument("--interval", default=None, help="Force interval (1m|5m|15m|1h|1d)")
    parser.add_argument("--period", default=None, help="yfinance period (e.g. '1d', '5d')")
    args = parser.parse_args()

    logger.remove()
    logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level: <5} | {message}")

    if args.once:
        interval = args.interval or (INTRADAY_INTERVAL if in_session() else EOD_INTERVAL)
        period = args.period or (INTRADAY_BACKFILL_PERIOD if interval.endswith("m") else "5d")

        async def _once() -> None:
            symbols = await load_active_symbols()
            logger.info(f"once: syms={len(symbols)} interval={interval} period={period}")
            inserted = await poll_once(symbols, interval=interval, period=period)
            logger.info(f"once done: inserted={inserted}")

        asyncio.run(_once())
        return

    logger.info("us bar poller starting")
    asyncio.run(run())


if __name__ == "__main__":
    main()

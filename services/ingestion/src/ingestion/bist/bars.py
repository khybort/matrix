"""yfinance bar poller — writes OHLCV bars to `market_bars` (asset_class='bist').

Schedule:
  * During TR session (Mon-Fri 10:00-18:00 Europe/Istanbul): 1m bars every 60s.
    yfinance's `period='1d', interval='1m'` returns the rolling intraday window;
    we upsert by (symbol, interval, ts) so re-polling is idempotent.
  * Outside session: 1d bars once an hour. The EOD bar finalizes after close
    and we keep it fresh for downstream backtesters.

Bulk download: yfinance lets us hit `yf.download([t1, t2, ...])` in one call,
which is far below the per-symbol rate ceiling. Universe is batched into
groups of BATCH_SIZE to keep response payloads sane.

Failure semantics: any batch that fails is logged and skipped; the next
poll cycle retries from scratch. Yahoo silently omits unknown tickers from
the result; we tolerate that and move on.
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
import uuid
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf
from loguru import logger
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from matrix_shared import session_scope
from matrix_shared.models import BistSymbol, MarketBar

TR = ZoneInfo("Europe/Istanbul")
SESSION_OPEN = time(10, 0)
SESSION_CLOSE = time(18, 0)

BATCH_SIZE = 50  # symbols per yfinance call; keeps response payloads manageable
INTRADAY_INTERVAL = "1m"
EOD_INTERVAL = "1d"
INTRADAY_POLL_S = 60
EOD_POLL_S = 3600


def in_session(now: datetime | None = None) -> bool:
    """Return True if `now` is within the BIST session window."""
    now = now or datetime.now(TR)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC).astimezone(TR)
    else:
        now = now.astimezone(TR)
    if now.weekday() >= 5:  # Sat/Sun
        return False
    return SESSION_OPEN <= now.time() < SESSION_CLOSE


async def load_active_symbols() -> list[str]:
    async with session_scope() as session:
        rows = await session.execute(
            select(BistSymbol.symbol).where(BistSymbol.active.is_(True))
        )
        return sorted({r[0] for r in rows})


def _yahoo_ticker(symbol: str) -> str:
    return f"{symbol}.IS"


def _batched(items: list[str], n: int) -> list[list[str]]:
    return [items[i : i + n] for i in range(0, len(items), n)]


def _fetch_batch(tickers: list[str], *, interval: str, period: str) -> pd.DataFrame:
    """Single bulk yfinance call. Returns multi-level dataframe or empty."""
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
    except Exception as e:  # yfinance throws bare Exception in some paths
        logger.warning(f"yfinance batch failed ({interval}, {len(tickers)} syms): {e}")
        return pd.DataFrame()
    return df if df is not None else pd.DataFrame()


def _rows_from_batch(
    df: pd.DataFrame, tickers: list[str], *, interval: str
) -> list[dict]:
    if df.empty:
        return []

    rows: list[dict] = []
    now = datetime.now(UTC)
    # When multiple tickers are requested, yfinance returns a 2-level column index
    # keyed by ticker. Single-ticker returns a flat frame.
    multi = isinstance(df.columns, pd.MultiIndex)

    for ticker in tickers:
        try:
            tdf = df[ticker] if multi else df
        except KeyError:
            continue
        if tdf is None or tdf.empty:
            continue

        symbol = ticker.removesuffix(".IS")
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
                    "symbol": symbol,
                    "asset_class": "bist",
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
    async with session_scope() as session:
        stmt = pg_insert(MarketBar).values(rows)
        stmt = stmt.on_conflict_do_nothing(
            constraint="uq_market_bars_class_sit"
        )
        result = await session.execute(stmt)
        return result.rowcount or 0


async def poll_once(symbols: list[str], *, interval: str, period: str) -> int:
    """One pass over the universe; returns inserted bar count."""
    if not symbols:
        return 0
    tickers = [_yahoo_ticker(s) for s in symbols]
    total = 0
    for batch in _batched(tickers, BATCH_SIZE):
        df = await asyncio.to_thread(_fetch_batch, batch, interval=interval, period=period)
        rows = _rows_from_batch(df, batch, interval=interval)
        inserted = await _upsert(rows)
        total += inserted
        logger.debug(
            f"batch {batch[0]}…+{len(batch) - 1}: rows={len(rows)} inserted={inserted}"
        )
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
    while not stop.is_set():
        symbols = await load_active_symbols()
        if not symbols:
            logger.warning("no active BIST symbols — run `matrix-bist-symbols` to seed")
            await asyncio.sleep(60)
            continue

        if in_session():
            t0 = datetime.now(UTC)
            inserted = await poll_once(symbols, interval=INTRADAY_INTERVAL, period="1d")
            dt = (datetime.now(UTC) - t0).total_seconds()
            logger.info(
                f"intraday poll: syms={len(symbols)} inserted={inserted} took={dt:.1f}s"
            )
            sleep_s = INTRADAY_POLL_S
        else:
            now = datetime.now(UTC)
            stale = last_eod_pull is None or (now - last_eod_pull) > timedelta(hours=1)
            if stale:
                inserted = await poll_once(symbols, interval=EOD_INTERVAL, period="5d")
                last_eod_pull = now
                logger.info(f"EOD poll: syms={len(symbols)} inserted={inserted}")
            sleep_s = EOD_POLL_S

        try:
            await asyncio.wait_for(stop.wait(), timeout=sleep_s)
        except TimeoutError:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Matrix BIST bar poller (yfinance)")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Poll one cycle and exit (smoketest)",
    )
    parser.add_argument(
        "--interval",
        default=None,
        help="Force interval (1m|5m|15m|1h|1d); default auto from session",
    )
    parser.add_argument(
        "--period",
        default=None,
        help="yfinance period (e.g. '1d', '5d'); default auto",
    )
    args = parser.parse_args()

    logger.remove()
    logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level: <5} | {message}")

    if args.once:
        interval = args.interval or (INTRADAY_INTERVAL if in_session() else EOD_INTERVAL)
        period = args.period or ("1d" if interval.endswith("m") else "5d")

        async def _once() -> None:
            symbols = await load_active_symbols()
            logger.info(f"once: syms={len(symbols)} interval={interval} period={period}")
            inserted = await poll_once(symbols, interval=interval, period=period)
            logger.info(f"once done: inserted={inserted}")

        asyncio.run(_once())
        return

    logger.info("bist bar poller starting")
    asyncio.run(run())


if __name__ == "__main__":
    main()

"""Deribit option-IV term recorder: the input of the r5f forward test.

Round 5 cell D.3 (front-end IV inversion -> long BTC / ETH perp 3 days) is
forward-tested at n = 60 (services/backtest/research/signal_2026_10_r5f).
Its signal is a 365-day percentile of TERM = front ATM IV - back ATM IV from
the Deribit option trades in [D-4h, D) of each 00:00 UTC decision day D
(matrix_shared.iv_term). This writes one `deribit_iv_daily` row per
(currency, D) (local tier, migration 0043) with the same endpoint, window and
construction as round 5's fetch.py:

  history.deribit.com public/get_last_trades_by_currency_and_time
  kind=option, [D-4h, D), count=1000, ascending, deduplicated by trade_id.

Every INTERVAL_S it finds the days in [today - BACKFILL_DAYS, today] that have
no row and fetches them newest first: today's row lands minutes after 00:00 UTC
(before the module's 01:00 entry), a fresh node backfills the 365-day history
the percentile needs, and a day missed during an outage is filled in later, so
the series the forward test is evaluated on has no holes. A day whose window
had no trades is a row with n_trades 0; a fetch that fails writes nothing and
is retried. Public endpoint, no keys, >= PAUSE_S between calls.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, date, datetime, timedelta

import httpx
from loguru import logger
from sqlalchemy import text

from matrix_shared import iv_term, local_session_scope

ENABLED = os.environ.get("IV_TERM_RECORDER_ENABLED", "true").strip().lower() != "false"
INTERVAL_S = float(os.environ.get("IV_TERM_RECORDER_INTERVAL_S", "900"))
BACKFILL_DAYS = int(os.environ.get("IV_TERM_RECORDER_BACKFILL_DAYS", "400"))
SETTLE_S = float(os.environ.get("IV_TERM_RECORDER_SETTLE_S", "120"))  # after 00:00 before today's window is read
PAUSE_S = 0.35

_URL = (
    "https://history.deribit.com/api/v2/public/get_last_trades_by_currency_and_time"
    "?currency={cur}&kind=option&start_timestamp={t0}&end_timestamp={t1}&count=1000&sorting=asc"
)


async def _get(client: httpx.AsyncClient, url: str) -> dict:
    err = ""
    for attempt in range(4):
        try:
            r = await client.get(url, timeout=30)
            if r.status_code == 429:
                err = "429"
                await asyncio.sleep(float(r.headers.get("retry-after", 10)))
                continue
            r.raise_for_status()
            await asyncio.sleep(PAUSE_S)
            return r.json()["result"]
        except Exception as e:  # noqa: BLE001 — network: retry, then give up this day
            err = str(e)
            await asyncio.sleep(5 * (attempt + 1))
    raise RuntimeError(f"deribit unavailable: {err}")


async def fetch_window(client: httpx.AsyncClient, cur: str, day_ms: int) -> list[dict]:
    """Every option trade of `cur` in [D-4h, D), as round 5's fetch.py paged it."""
    t0, t1 = day_ms - iv_term.WINDOW_H * iv_term.H_MS, day_ms
    keep: dict[int | str, dict] = {}
    cursor = t0
    while True:
        r = await _get(client, _URL.format(cur=cur, t0=cursor, t1=t1 - 1))
        tr = r.get("trades") or []
        for x in tr:
            keep[x["trade_id"]] = x
        if not r.get("has_more") or not tr:
            break
        last = tr[-1]["timestamp"]
        cursor = max(last, cursor + 1) if last == cursor else last  # same-ms trades re-served; deduped above
    return list(keep.values())


def missing_days(have: set[tuple[str, date]], today: date) -> list[tuple[str, date]]:
    """(currency, day) without a row in [today - BACKFILL_DAYS, today], newest first."""
    out = []
    for k in range(BACKFILL_DAYS + 1):
        d = today - timedelta(days=k)
        out += [(c, d) for c in iv_term.CURRENCIES if (c, d) not in have]
    return out


async def _have(since: date) -> set[tuple[str, date]]:
    async with local_session_scope() as s:
        rows = (await s.execute(
            text("SELECT currency, day FROM deribit_iv_daily WHERE day >= :since"), {"since": since}
        )).all()
    return {(r[0], r[1]) for r in rows}


async def _write(cur: str, day: date, f: dict) -> None:
    async with local_session_scope() as s:
        await s.execute(
            text(
                "INSERT INTO deribit_iv_daily (currency, day, n_trades, n_front, n_back, front_iv, back_iv, "
                "term, put_notional, call_notional) VALUES (:c, :d, :n, :nf, :nb, :f, :b, :t, :p, :cl) "
                "ON CONFLICT (currency, day) DO NOTHING"
            ),
            {"c": cur, "d": day, "n": f["n_trades"], "nf": f["n_front"], "nb": f["n_back"],
             "f": f["front_iv"], "b": f["back_iv"], "t": f["term"], "p": f["put_notional"],
             "cl": f["call_notional"]},
        )


async def record_pass(client: httpx.AsyncClient, now: datetime | None = None) -> tuple[int, int]:
    """Fetch and write every missing day. (written, failed)."""
    now = now or datetime.now(UTC)
    today = now.date()
    if (now - iv_term.day_floor(now)).total_seconds() < SETTLE_S:
        today -= timedelta(days=1)  # today's window closed seconds ago; next pass
    todo = missing_days(await _have(today - timedelta(days=BACKFILL_DAYS)), today)
    written = failed = 0
    for cur, d in todo:
        day_ms = iv_term.to_ms(datetime(d.year, d.month, d.day, tzinfo=UTC))
        try:
            trades = await fetch_window(client, cur, day_ms)
        except Exception as e:  # noqa: BLE001 — retried next pass
            failed += 1
            logger.warning(f"iv term recorder: {cur} {d} fetch failed: {e}")
            continue
        f = iv_term.term_features(trades, day_ms)
        await _write(cur, d, f)
        written += 1
        if d == today:
            term = f"{f['term']:+.2f}" if f["term"] is not None else "missing"
            logger.info(
                f"iv term recorder: {cur} {d} TERM {term} (front {f['n_front']} / back {f['n_back']} ATM trades "
                f"of {f['n_trades']})"
            )
        elif written % 100 == 0:
            logger.info(f"iv term recorder: backfill {written} of {len(todo)} days written (at {cur} {d})")
    return written, failed


async def run() -> None:
    if not ENABLED:
        logger.info("iv term recorder disabled (IV_TERM_RECORDER_ENABLED=false)")
        return
    logger.info(f"iv term recorder start: every {INTERVAL_S:.0f}s, backfill {BACKFILL_DAYS} d")
    async with httpx.AsyncClient(headers={"User-Agent": "matrix-ingestion/1.0"}) as client:
        while True:
            try:
                n, bad = await record_pass(client)
                if n or bad:
                    logger.info(f"iv term recorder: wrote {n} day rows, {bad} failed")
            except Exception as e:  # noqa: BLE001 — table missing / DB blip: retry next pass
                logger.warning(f"iv term recorder pass failed: {e}")
            await asyncio.sleep(INTERVAL_S)

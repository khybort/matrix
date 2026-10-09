"""Margin borrow-rate recorder: the history the venues do not publish.

`neg_funding_carry` borrows the coin to short spot, and its edge depends on what
that borrow costs while a squeeze lasts (docs/wiki/signal-research-2026-10.md,
"Borrow measurement"). Bybit and Binance publish only the current rate, so this
polls the same public tables the strategy reads every INTERVAL_S and writes ALL
coins to `margin_borrow_rates` (local tier, migration 0041):

  * Bybit `/v5/spot-margin-trade/data` (VIP0): hourlyBorrowRate,
    maxBorrowingAmount, borrowable.
  * Binance `bapi/margin/v1/public/margin/vip/spec/list-all` (VIP0):
    dailyInterestRate / 24, borrowLimit.

A row is written when a coin's rate, limit or borrowable flag changed since the
last row written, or that row is HEARTBEAT_S old, so the series is a step
function a reader can trust: no row for > HEARTBEAT_S + INTERVAL_S means the
recorder was down, not that the rate held. `max_borrow` is a per-account limit,
not pool size; neither venue exposes utilisation publicly.

Rows older than RETENTION_DAYS are deleted hourly through the `ts` index.
Public REST only, no keys.
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
from loguru import logger
from sqlalchemy import text

from matrix_shared import local_session_scope

ENABLED = os.environ.get("BORROW_RECORDER_ENABLED", "true").strip().lower() != "false"
INTERVAL_S = float(os.environ.get("BORROW_RECORDER_INTERVAL_S", "600"))
HEARTBEAT_S = float(os.environ.get("BORROW_RECORDER_HEARTBEAT_S", "3600"))
RETENTION_DAYS = float(os.environ.get("BORROW_RECORDER_RETENTION_DAYS", "180"))
PRUNE_EVERY_S = 3600.0

_BYBIT_MARGIN = "https://api.bybit.com/v5/spot-margin-trade/data?vipLevel=No%20VIP"
_BINANCE_MARGIN = "https://www.binance.com/bapi/margin/v1/public/margin/vip/spec/list-all"

Quote = tuple[Decimal, Decimal | None, bool]  # hourly rate, max borrow (coin units), borrowable
Key = tuple[str, str]  # venue, coin


def _dec(v) -> Decimal | None:
    try:
        return Decimal(str(v)) if v not in (None, "") else None
    except ArithmeticError:
        return None


def parse_tables(bybit: dict | None, binance: dict | None) -> dict[Key, Quote]:
    """(venue, coin) -> quote, every coin with a rate (borrowable or not)."""
    out: dict[Key, Quote] = {}
    for group in ((bybit or {}).get("result") or {}).get("vipCoinList") or []:
        for c in group.get("list") or []:
            rate = _dec(c.get("hourlyBorrowRate"))
            if c.get("currency") and rate is not None:
                out[("bybit", c["currency"])] = (rate, _dec(c.get("maxBorrowingAmount")), bool(c.get("borrowable")))
    for a in (binance or {}).get("data") or []:
        vip0 = next((s for s in a.get("specs") or [] if str(s.get("vipLevel")) == "0"), None)
        daily = _dec((vip0 or {}).get("dailyInterestRate"))
        if a.get("assetName") and daily is not None:
            out[("binance", a["assetName"])] = (daily / 24, _dec(vip0.get("borrowLimit")), True)
    return out


def due(quotes: dict[Key, Quote], last: dict[Key, tuple[Quote, float]], now_s: float) -> list[Key]:
    """Keys whose quote changed or whose last written row is HEARTBEAT_S old."""
    return [
        k for k, q in quotes.items()
        if k not in last or last[k][0] != q or now_s - last[k][1] >= HEARTBEAT_S
    ]


async def _fetch(client: httpx.AsyncClient) -> tuple[dict | None, dict | None]:
    docs: list[dict | None] = []
    for url in (_BYBIT_MARGIN, _BINANCE_MARGIN):
        try:
            r = await client.get(url, timeout=15)
            r.raise_for_status()
            docs.append(r.json())
        except Exception as e:  # noqa: BLE001 — one venue down leaves the other
            logger.warning(f"borrow recorder: {url.split('/')[2]} unavailable: {e}")
            docs.append(None)
    return docs[0], docs[1]


async def _load_last() -> dict[Key, tuple[Quote, float]]:
    async with local_session_scope() as s:
        rows = (await s.execute(text(
            "SELECT DISTINCT ON (venue, coin) venue, coin, hourly_rate, max_borrow, borrowable, ts "
            "FROM margin_borrow_rates WHERE ts > now() - interval '2 hours' "
            "ORDER BY venue, coin, ts DESC"
        ))).all()
    return {(r[0], r[1]): ((r[2], r[3], r[4]), r[5].timestamp()) for r in rows}


async def record_once(client: httpx.AsyncClient, last: dict[Key, tuple[Quote, float]]) -> tuple[int, int]:
    """Poll both tables, write changed/heartbeat rows. (written, coins seen)."""
    quotes = parse_tables(*await _fetch(client))
    now = datetime.now(UTC)
    keys = due(quotes, last, now.timestamp())
    if keys:
        async with local_session_scope() as s:
            await s.execute(
                text(
                    "INSERT INTO margin_borrow_rates (venue, coin, ts, hourly_rate, max_borrow, borrowable) "
                    "VALUES (:venue, :coin, :ts, :rate, :maxb, :ok) ON CONFLICT DO NOTHING"
                ),
                [{"venue": v, "coin": c, "ts": now, "rate": quotes[(v, c)][0],
                  "maxb": quotes[(v, c)][1], "ok": quotes[(v, c)][2]} for v, c in keys],
            )
        for k in keys:
            last[k] = (quotes[k], now.timestamp())
    return len(keys), len(quotes)


async def prune() -> int:
    cutoff = datetime.now(UTC) - timedelta(days=RETENTION_DAYS)
    async with local_session_scope() as s:
        res = await s.execute(text("DELETE FROM margin_borrow_rates WHERE ts < :cut"), {"cut": cutoff})
    return res.rowcount or 0


async def run() -> None:
    if not ENABLED:
        logger.info("borrow recorder disabled (BORROW_RECORDER_ENABLED=false)")
        return
    logger.info(f"borrow recorder start: every {INTERVAL_S:.0f}s, heartbeat {HEARTBEAT_S:.0f}s, keep {RETENTION_DAYS:g}d")
    last: dict[Key, tuple[Quote, float]] = {}
    try:
        last = await _load_last()
    except Exception as e:  # noqa: BLE001 — table missing = migration not applied; retry each tick
        logger.warning(f"borrow recorder: cannot read margin_borrow_rates ({e})")
    pruned_at = 0.0
    async with httpx.AsyncClient() as client:
        while True:
            try:
                n, seen = await record_once(client, last)
                logger.info(f"borrow recorder: wrote {n} of {seen} coin quotes")
                if time.monotonic() - pruned_at >= PRUNE_EVERY_S:
                    d = await prune()
                    pruned_at = time.monotonic()
                    if d:
                        logger.info(f"borrow recorder: pruned {d} rows older than {RETENTION_DAYS:g}d")
            except Exception as e:  # noqa: BLE001 — never let one bad poll kill the loop
                logger.warning(f"borrow recorder poll failed: {e}")
            await asyncio.sleep(INTERVAL_S)

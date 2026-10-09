"""Book-priced accounting for spot-hedged carries (neg_funding_carry).

A flat `round_trip_cost_pct x 2` made a short-spot carry on an illiquid coin
look as cheap as BTC. The adversarial check of 2026-10-09
(docs/wiki/signal-research-2026-10.md) priced the same episodes on real books:
median four-leg cost 64 bps at $500 per leg against the 30 bps the study
assumed, and a near-zero edge at $5k. So a carry whose prediction names its
spot leg (`context.spot_venue` / `spot_symbol`) is opened and closed on the
books instead:

- open: both books must exist (no spot book -> no hedge -> no position); the
  per-leg size is capped where every walk stays within MAX_LEG_IMPACT_BPS of
  mid, and at UNCONFIRMED_MAX_LEG_USD until the strategy's promotion status is
  `confirmed`; the impact of the two opening fills is recorded.
- close: the two closing fills are walked on the books at close (falling back
  to the estimate taken at open when a book is gone; a book too thin to fill
  is walked and the remainder charged at its worst level + a penalty, never
  below the estimate: `close_source = thin_book`), plus four taker fees.
- borrow: per started hour, as margin desks charge it, at the rate the venue
  quoted for that hour in `margin_borrow_rates` (ingestion.borrow_recorder);
  an hour the series does not cover falls back to the entry quote x
  BORROW_STRESS. `borrow_source` says which: series / stressed_entry / mixed.

Book maths duplicates strategy.modules.crypto.neg_funding_carry on purpose:
the two services ship as separate images and share no package but
matrix_shared.
"""

from __future__ import annotations

import asyncio
import math
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
from loguru import logger
from sqlalchemy import text

from matrix_shared import local_session_scope

BORROW_STRESS = float(os.environ.get("MATRIX_CARRY_BORROW_STRESS", "3"))
MAX_LEG_IMPACT_BPS = float(os.environ.get("MATRIX_CARRY_MAX_LEG_IMPACT_BPS", "10"))
UNCONFIRMED_MAX_LEG_USD = float(os.environ.get("MATRIX_CARRY_UNCONFIRMED_MAX_LEG_USD", "500"))
MIN_LEG_USD = float(os.environ.get("MATRIX_CARRY_MIN_LEG_USD", "50"))
PERP_TAKER_BPS = float(os.environ.get("MATRIX_CARRY_PERP_TAKER_BPS", "5.5"))
SPOT_TAKER_BPS = float(os.environ.get("MATRIX_CARRY_SPOT_TAKER_BPS", "10"))
BOOK_MAX_AGE_S = 30.0
# A closing leg the book now present cannot fill (`walk_bps` None) is the
# squeeze case: depth vanished on exactly the coin whose short is being bought
# back. The open-time estimate is the calm-day price, so it is never used for
# it: the visible depth is walked and the rest is charged at the worst visible
# level plus this penalty, never below the estimate.
THIN_BOOK_PENALTY_BPS = float(os.environ.get("MATRIX_CARRY_THIN_BOOK_PENALTY_BPS", "50"))

Levels = list[tuple[float, float]]  # (price, qty), best first


@dataclass(slots=True)
class Book:
    bids: Levels
    asks: Levels
    source: str

    @property
    def mid(self) -> float:
        return (self.bids[0][0] + self.asks[0][0]) / 2


def is_book_priced(context: dict | None) -> bool:
    return bool(context and context.get("spot_venue") and context.get("spot_symbol"))


def walk_bps(levels: Levels, mid: float, usd: float) -> float | None:
    """Average distance from mid (bps) of a taker fill of `usd` walking
    `levels`; None when the book is too thin to fill it."""
    if usd <= 0 or mid <= 0:
        return 0.0
    rem, cost = usd, 0.0
    for p, q in levels:
        take = min(rem, p * q)
        cost += take * abs(p - mid) / mid
        rem -= take
        if rem <= 1e-9:
            return cost / usd * 1e4
    return None


def thin_walk_bps(levels: Levels, mid: float, usd: float, penalty_bps: float | None = None) -> float:
    """Average distance from mid (bps) of a taker fill of `usd` on a book too
    thin to fill it: the visible levels as walked, the remainder at the worst
    visible level's distance + `penalty_bps` (THIN_BOOK_PENALTY_BPS)."""
    penalty = THIN_BOOK_PENALTY_BPS if penalty_bps is None else penalty_bps
    if usd <= 0 or mid <= 0:
        return 0.0
    rem, cost, worst = usd, 0.0, 0.0
    for p, q in levels:
        take = min(rem, p * q)
        d = abs(p - mid) / mid
        cost += take * d
        worst = max(worst, d)
        rem -= take
        if rem <= 1e-9:
            return cost / usd * 1e4
    return (cost + rem * (worst + penalty / 1e4)) / usd * 1e4


def max_usd_within(levels: Levels, mid: float, max_bps: float) -> float:
    """Largest taker notional whose average distance from mid stays within
    `max_bps`, bounded by visible depth."""
    lim = max_bps / 1e4
    vol = cost = 0.0
    for p, q in levels:
        s = abs(p - mid) / mid
        c = p * q
        if s > lim:
            x = (lim * vol - cost) / (s - lim)  # take that brings the average to the limit
            if x < c:
                return vol + max(0.0, x)
        vol += c
        cost += c * s
    return vol


def leg_cap_usd(perp: Book, spot: Book, max_bps: float = MAX_LEG_IMPACT_BPS) -> float:
    return min(
        max_usd_within(perp.asks, perp.mid, max_bps),
        max_usd_within(perp.bids, perp.mid, max_bps),
        max_usd_within(spot.bids, spot.mid, max_bps),
        max_usd_within(spot.asks, spot.mid, max_bps),
    )


def fees_bps() -> float:
    return 2 * PERP_TAKER_BPS + 2 * SPOT_TAKER_BPS


def parse_levels(raw) -> Levels:
    return [(float(p), float(q)) for p, q, *_ in raw or [] if float(q) > 0]


_BOOK_URLS = {
    ("bybit", "linear"): "https://api.bybit.com/v5/market/orderbook?category=linear&symbol={s}&limit=200",
    ("bybit", "spot"): "https://api.bybit.com/v5/market/orderbook?category=spot&symbol={s}&limit=200",
    ("binance", "spot"): "https://api.binance.com/api/v3/depth?symbol={s}&limit=100",
}
_DB_EXCHANGE = {("bybit", "linear"): "bybit", ("bybit", "spot"): "bybit-spot", ("binance", "spot"): "binance-spot"}


async def db_book(venue: str, category: str, symbol: str) -> Book | None:
    """Ingested snapshot <= BOOK_MAX_AGE_S old; one indexed read, no network."""
    if (venue, category) not in _DB_EXCHANGE:
        return None
    async with local_session_scope() as session:
        row = (
            await session.execute(
                text(
                    "SELECT bids, asks FROM market_orderbook_snapshots WHERE exchange = :ex "
                    "AND symbol = :sym AND snapshot_ts > :since ORDER BY snapshot_ts DESC LIMIT 1"
                ),
                {"ex": _DB_EXCHANGE[(venue, category)], "sym": symbol,
                 "since": datetime.now(UTC) - timedelta(seconds=BOOK_MAX_AGE_S)},
            )
        ).first()
    if row is not None:
        b, a = parse_levels(row[0]), parse_levels(row[1])
        if b and a:
            return Book(b, a, "db")
    return None


async def rest_book(venue: str, category: str, symbol: str, timeout_s: float = 10.0) -> Book | None:
    """The venue's public REST depth; None on any failure."""
    if (venue, category) not in _BOOK_URLS:
        return None
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            r = await client.get(_BOOK_URLS[(venue, category)].format(s=symbol))
            r.raise_for_status()
            j = r.json()
    except Exception as e:  # noqa: BLE001 — a missing book refuses the open / falls back at close
        logger.debug(f"carry_books: {venue} {category} {symbol} book unavailable: {e}")
        return None
    if venue == "bybit":
        d = j.get("result") or {}
        b, a = parse_levels(d.get("b")), parse_levels(d.get("a"))
    else:
        b, a = parse_levels(j.get("bids")), parse_levels(j.get("asks"))
    return Book(b, a, "rest") if b and a else None


async def fetch_book(venue: str, category: str, symbol: str, *, allow_rest: bool = True) -> Book | None:
    """Ingested snapshot <= 30 s old, else (when allowed) the venue's REST depth."""
    book = await db_book(venue, category, symbol)
    if book is None and allow_rest:
        book = await rest_book(venue, category, symbol)
    return book


def _leg_keys(symbol: str, context: dict) -> tuple[tuple[str, str, str], tuple[str, str, str]]:
    return ("bybit", "linear", symbol), (str(context["spot_venue"]), "spot", str(context["spot_symbol"]))


async def legs(symbol: str, context: dict, *, allow_rest: bool = True) -> tuple[Book | None, Book | None]:
    perp_k, spot_k = _leg_keys(symbol, context)
    perp = await fetch_book(*perp_k, allow_rest=allow_rest)
    spot = await fetch_book(*spot_k, allow_rest=allow_rest)
    return perp, spot


# Overall budget for the REST books of every carry closing in one tick. A close
# never waits longer: a leg without a book by then is priced at the estimate
# recorded at open (`close_source = entry_estimate`).
CLOSE_BOOK_TIMEOUT_S = float(os.environ.get("MATRIX_CARRY_CLOSE_BOOK_TIMEOUT_S", "3"))


async def legs_for_close(carries: dict, timeout_s: float | None = None) -> dict:
    """{key: (symbol, context)} -> {key: (perp, spot)}. DB books first; legs
    without one are fetched over REST concurrently, all within `timeout_s`
    (default CLOSE_BOOK_TIMEOUT_S) together; whatever has not answered is None."""
    timeout_s = CLOSE_BOOK_TIMEOUT_S if timeout_s is None else timeout_s
    want: dict[tuple[str, str, str], Book | None] = {}
    for symbol, ctx in carries.values():
        for k in _leg_keys(symbol, ctx):
            if k not in want:
                want[k] = await db_book(*k)
    tasks = {asyncio.create_task(rest_book(*k, timeout_s=timeout_s)): k for k, b in want.items() if b is None}
    if tasks:
        done, pending = await asyncio.wait(tasks, timeout=timeout_s)
        for t in pending:
            t.cancel()
        for t in done:
            want[tasks[t]] = t.result()  # rest_book never raises
        if pending:
            logger.warning(f"carry_books: {len(pending)} REST book(s) not back in {timeout_s:.0f}s; "
                           "closing on the open-time estimate")
    out = {}
    for key, (symbol, ctx) in carries.items():
        perp_k, spot_k = _leg_keys(symbol, ctx)
        out[key] = (want[perp_k], want[spot_k])
    return out


def size_and_price_open(
    perp: Book, spot: Book, notional: float, *, confirmed: bool
) -> tuple[float, dict] | tuple[None, str]:
    """(per-leg notional, context patch) or (None, refusal reason). `notional`
    is what the engine's own sizing and risk gate allow; this only lowers it."""
    cap = leg_cap_usd(perp, spot)
    ceiling = math.inf if confirmed else UNCONFIRMED_MAX_LEG_USD
    usd = min(notional, cap, ceiling)
    if usd < MIN_LEG_USD:
        return None, f"leg size {usd:.0f} < {MIN_LEG_USD:.0f} (impact cap {cap:.0f})"
    walks = {
        "perp_buy_bps": walk_bps(perp.asks, perp.mid, usd),
        "spot_sell_bps": walk_bps(spot.bids, spot.mid, usd),
        # estimates of the closing fills on today's book: the fallback when a
        # book is missing at close
        "perp_sell_est_bps": walk_bps(perp.bids, perp.mid, usd),
        "spot_buy_est_bps": walk_bps(spot.asks, spot.mid, usd),
    }
    if any(v is None for v in walks.values()):
        return None, "book too thin"
    patch = {
        "notional_usd": round(usd, 2),
        "leg_cap_usd": round(cap, 2),
        "ceiling_usd": None if confirmed else UNCONFIRMED_MAX_LEG_USD,
        "sources": {"perp": perp.source, "spot": spot.source},
        "borrow_stress": BORROW_STRESS,
        **{k: round(v, 3) for k, v in walks.items()},
    }
    return usd, patch


def _close_leg(levels: Levels | None, mid: float, usd: float, est: float) -> tuple[float, str]:
    """(bps, source) of one closing fill: the walk on the book now; the
    open-time estimate when there is no book; on a book too thin to fill it,
    the penalised walk, never below the estimate."""
    if levels is None:
        return est, "entry_estimate"
    w = walk_bps(levels, mid, usd)
    if w is not None:
        return w, "book"
    return max(est, thin_walk_bps(levels, mid, usd)), "thin_book"


def close_cost_bps(book_open: dict, perp: Book | None, spot: Book | None, usd: float) -> dict:
    """Four taker fees + the two recorded opening walks + the two closing walks
    (on the books now; the estimate taken at open when a book is gone; a
    penalised walk, >= the estimate, when a book is too thin to fill)."""
    perp_sell, perp_src = _close_leg(perp.bids if perp else None, perp.mid if perp else 0.0, usd,
                                     float(book_open["perp_sell_est_bps"]))
    spot_buy, spot_src = _close_leg(spot.asks if spot else None, spot.mid if spot else 0.0, usd,
                                    float(book_open["spot_buy_est_bps"]))
    srcs = {perp_src, spot_src}
    out = {
        "fees_bps": fees_bps(),
        "perp_buy_bps": float(book_open["perp_buy_bps"]),
        "spot_sell_bps": float(book_open["spot_sell_bps"]),
        "perp_sell_bps": perp_sell,
        "spot_buy_bps": spot_buy,
        # most conservative leg names the close
        "close_source": next(x for x in ("thin_book", "entry_estimate", "book") if x in srcs),
    }
    out["total_bps"] = round(sum(v for k, v in out.items() if k.endswith("_bps")), 3)
    return out


def mark_cost_bps(book_open: dict, perp: Book | None, spot: Book | None, usd: float) -> float:
    """Cost (bps) an open carry is marked at for the equity curve: the
    close_cost_bps total, but each closing walk at the WORSE of the book now
    and the estimate recorded at open. A close charges one of the two, so the
    mark is never cheaper than closing on the same books would be."""
    c = close_cost_bps(book_open, perp, spot, usd)
    return c["total_bps"] + max(0.0, float(book_open["perp_sell_est_bps"]) - c["perp_sell_bps"]) + max(
        0.0, float(book_open["spot_buy_est_bps"]) - c["spot_buy_bps"]
    )


def borrow_charge(notional: Decimal, hourly: Decimal, stress: float, held_s: float) -> Decimal:
    """Borrow at the quoted hourly rate x stress, per started hour."""
    hours = math.ceil(max(0.0, held_s) / 3600.0 - 1e-9)
    return notional * hourly * Decimal(str(stress)) * hours


# A recorded quote stays valid this long after its row: the recorder writes a
# heartbeat row every hour and polls every 10 min (ingestion.borrow_recorder).
SERIES_MAX_AGE = timedelta(minutes=75)


def series_rates(rows: list[tuple[datetime, Decimal]], opened: datetime, now: datetime) -> list[Decimal | None]:
    """Rate for each started hour of the hold from the recorded step series
    (`rows` ts ascending): the quote in force at the hour's start, else the
    first quote recorded inside that hour; None for an hour the series misses."""
    hours = math.ceil(max(0.0, (now - opened).total_seconds()) / 3600.0 - 1e-9)
    out: list[Decimal | None] = []
    j = -1  # index of the latest row at or before the hour start
    for i in range(hours):
        start = opened + timedelta(hours=i)
        end = min(start + timedelta(hours=1), now)
        while j + 1 < len(rows) and rows[j + 1][0] <= start:
            j += 1
        if j >= 0 and start - rows[j][0] <= SERIES_MAX_AGE:
            out.append(rows[j][1])
        elif j + 1 < len(rows) and rows[j + 1][0] <= end:
            out.append(rows[j + 1][1])
        else:
            out.append(None)
    return out


def borrow_from_series(
    notional: Decimal, rates: list[Decimal | None], entry_hourly: Decimal, stress: float
) -> tuple[Decimal, dict]:
    """Charge each started hour at its recorded rate (x1: a measured quote
    needs no stress); hours without one at the entry quote x stress."""
    fallback = entry_hourly * Decimal(str(stress))
    charge = sum((notional * (r if r is not None else fallback) for r in rates), Decimal("0"))
    n_series = sum(1 for r in rates if r is not None)
    source = "series" if rates and n_series == len(rates) else ("stressed_entry" if n_series == 0 else "mixed")
    return charge, {
        "borrow_source": source,
        "borrow_hours_series": n_series,
        "borrow_hours_fallback": len(rates) - n_series,
        "borrow_series_mean_hourly": str(sum(r for r in rates if r is not None) / n_series) if n_series else None,
    }


async def borrow_series_charge(
    notional: Decimal, venue: str, coin: str, opened: datetime, now: datetime,
    entry_hourly: Decimal, stress: float,
) -> tuple[Decimal, dict]:
    """Borrow for a carry's hold from `margin_borrow_rates`, falling back per
    hour to the entry quote x stress. A missing table charges the fallback."""
    rows: list[tuple[datetime, Decimal]] = []
    try:
        async with local_session_scope() as session:
            rows = [
                (r[0], Decimal(r[1]))
                for r in (
                    await session.execute(
                        text(
                            "SELECT ts, hourly_rate FROM margin_borrow_rates WHERE venue = :v AND coin = :c "
                            "AND ts >= :lo AND ts <= :hi ORDER BY ts"
                        ),
                        {"v": venue, "c": coin, "lo": opened - SERIES_MAX_AGE, "hi": now},
                    )
                ).all()
            ]
    except Exception as e:  # noqa: BLE001 — no series is the stressed fallback, not a failed close
        logger.warning(f"carry_books: borrow series {venue}/{coin} unavailable: {e}")
    return borrow_from_series(notional, series_rates(rows, opened, now), entry_hourly, stress)


async def carry_borrow(
    notional: Decimal, symbol: str, context: dict, opened: datetime, now: datetime
) -> tuple[Decimal, dict]:
    """Borrow a short-spot carry owes for its hold so far, and how it was
    priced. The one entry point for both the close and the open-carry equity
    mark, so the two cannot drift. (0, {}) when the carry borrows nothing."""
    borrow_h = (context or {}).get("borrow_rate_hourly")
    if not borrow_h:
        return Decimal("0"), {}
    stress = float((context.get("book_open") or {}).get("borrow_stress", BORROW_STRESS))
    venue = context.get("borrow_venue") or context.get("spot_venue")
    if venue:
        coin = str(context.get("spot_symbol") or symbol).removesuffix("USDT")
        return await borrow_series_charge(notional, str(venue), coin, opened, now, Decimal(str(borrow_h)), stress)
    return borrow_charge(notional, Decimal(str(borrow_h)), stress, (now - opened).total_seconds()), {
        "borrow_source": "stressed_entry"
    }

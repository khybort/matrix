"""Negative-funding carry on coins that can actually be shorted (neg_funding_carry) v1.

Research 2026-10 (docs/wiki/signal-research-2026-10.md): over a year of Bybit
settlements, a perp whose funding just *settled* at <= -0.08 % kept paying
longs for the next 48 h by far more than the four-leg cost. On coins with a
published borrow rate, net of 30 bps and that borrow: train (2025-10..2026-05)
+104 bps/episode, t=20, n=3 464; pre-registered holdout (2026-06..10-09)
+135 bps, t=10.2, n=1 105 (+65, t=4.9 at 3x the borrow rate). Median episode
only +13: the mean is carried by persistent squeezes. Borrow *availability*
is not in public data and is the open risk.
inverse_carry fires on the *predicted* rate of any coin, most of which
(LSK, SAGA, STEEM, ... on 2026-09) have no Bybit spot margin at all.

This module differs from inverse_carry in three ways, each from the study:
1. Entry on the last **settled** rate, not the live prediction (no look-ahead
   at a rate that can still move before the settlement).
2. Only coins with a published borrow rate on Bybit or Binance cross margin
   (public endpoints, cached 1 h). No table → no signal.
3. The hourly borrow rate rides in the context (`borrow_rate_hourly`) so the
   book can charge it; the study charged it at 1x and 3x today's rate.

Paper only, registered as `shadow`. Side `inverse_carry` so the paper engine's
carry accounting (settlement-paid funding) applies.

Entry filter (2026-10-09, adversarial check): the edge is about +20 bps per episode once
real spread and stressed borrow are charged, and almost all of the study's PnL
sat in squeezes whose quoted borrow was not believable. So at signal time the
module now prices the trade it would actually do, and skips it unless that
cost leaves most of the funding on the table:

    expected  = |settled rate| x settlements in 48 h (at the current interval)
    borrow    = quoted hourly borrow x 48 x MATRIX_CARRY_BORROW_STRESS (3)
    book      = 4 taker fees + the walked impact of all four legs (perp buy /
                sell, spot sell / buy) at the intended per-leg notional
    skip when borrow + book > expected x MATRIX_NFC_MAX_COST_SHARE (1/3)

The intended notional is the largest per-leg size at which every one of the
four walks stays within MATRIX_CARRY_MAX_LEG_IMPACT_BPS (10) of mid, capped at
MATRIX_CARRY_UNCONFIRMED_MAX_LEG_USD ($500). A missing spot book is a skip: a
carry without its hedge leg is a naked long perp. Every input rides in the
context so the paper book (which re-measures at open and close) and later
audits can see what the trade was priced at.

Funding decay (2026-10-09): over the study year the funding realised in the 48 h
after entry was a median 0.21 (train) / 0.30 (holdout) of the naive
`expected` above, because squeezes unwind. DECAY_RATIO holds train-fitted median
realised/naive ratios by (prior run of settlements <= -0.05 %, entry depth);
`expected_decayed_bps` and the verdict against it are recorded on every
signal. MATRIX_NFC_EXPECTED_MODEL=decay gates on it (cost <= decayed expected);
the default stays `naive`: on the holdout the decay gate raised net per kept
episode (+464 vs +293 bps) and survived 10x borrow, but kept 91 of 221 episodes
on 38 of 89 coins, 39 % less money at 3x borrow and more coin concentration
(docs/wiki/signal-research-2026-10.md, "Live path and funding decay").
"""

from __future__ import annotations

import os
import time
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
from loguru import logger
from sqlalchemy import text

from matrix_shared import local_session_scope, shared_session_scope
from matrix_shared.markets.crypto import crypto_universe

from strategy.base import PredictionDraft

STRATEGY_ID = "neg_funding_carry"
STRATEGY_VERSION = 1

DEFAULT_MIN_FUNDING = Decimal("0.0008")  # settled rate per interval, magnitude
FUNDING_CAP = Decimal("0.0030")  # magnitude that maps to confidence 1.0
DEFAULT_HORIZON_S = 172800  # 48 h, as studied
# Enter only while the settlement that triggered the signal is recent: the
# study entered at the settlement; hours later the next rate is half-priced in.
DEFAULT_MAX_SIGNAL_AGE_S = 3600

_BYBIT_MARGIN = "https://api.bybit.com/v5/spot-margin-trade/data?vipLevel=No%20VIP"
_BINANCE_MARGIN = "https://www.binance.com/bapi/margin/v1/public/margin/vip/spec/list-all"
_BORROW_TTL_S = 3600.0
_borrow_cache: tuple[float, dict[str, tuple[Decimal, str]]] | None = None

# Entry filter and sizing. Env names shared with backtest.carry_books so
# the strategy and the paper book price the same trade the same way.
BORROW_STRESS = float(os.environ.get("MATRIX_CARRY_BORROW_STRESS", "3"))
MAX_COST_SHARE = float(os.environ.get("MATRIX_NFC_MAX_COST_SHARE", str(1 / 3)))
MAX_LEG_IMPACT_BPS = float(os.environ.get("MATRIX_CARRY_MAX_LEG_IMPACT_BPS", "10"))
MAX_LEG_USD = float(os.environ.get("MATRIX_CARRY_UNCONFIRMED_MAX_LEG_USD", "500"))
MIN_LEG_USD = float(os.environ.get("MATRIX_CARRY_MIN_LEG_USD", "50"))
PERP_TAKER_BPS = float(os.environ.get("MATRIX_CARRY_PERP_TAKER_BPS", "5.5"))
SPOT_TAKER_BPS = float(os.environ.get("MATRIX_CARRY_SPOT_TAKER_BPS", "10"))
BOOK_MAX_AGE_S = 30.0
_INTERVALS_H = (1, 2, 4, 8)

# Funding-decay model. Realised 48 h funding / naive expectation, median per
# (run bucket, depth bucket) on the train split (2025-10..2026-05, 2 941
# book-sized episodes; cells with n < 30 back off to the run bucket). Run =
# consecutive settlements <= RUN_RATE right before the signal one: 0 / 1-2 / 3+.
# Depth = |settled rate| per interval: < 15 / 15-30 / 30-60 / >= 60 bps.
EXPECTED_MODEL = os.environ.get("MATRIX_NFC_EXPECTED_MODEL", "naive")
DECAY_MAX_COST_SHARE = float(os.environ.get("MATRIX_NFC_DECAY_MAX_COST_SHARE", "1.0"))
RUN_RATE = Decimal("-0.0005")
_DEPTH_EDGES = (Decimal("0.0015"), Decimal("0.003"), Decimal("0.006"))
DECAY_RATIO: dict[tuple[int, int], float] = {
    (0, 0): 0.212, (0, 1): 0.133, (0, 2): 0.100, (0, 3): 0.106,
    (1, 0): 0.300, (1, 1): 0.250, (1, 2): 0.129, (1, 3): 0.268,  # (1, 3): run back-off
    (2, 0): 0.615, (2, 1): 0.499, (2, 2): 0.345, (2, 3): 0.520,  # (2, 3): run back-off
}
_FUNDING_HISTORY = "https://api.bybit.com/v5/market/funding/history?category=linear&symbol={s}&limit=20"

Levels = list[tuple[float, float]]  # (price, qty), best first


def walk_bps(levels: Levels, mid: float, usd: float) -> float | None:
    """Average distance from mid, in bps, of a taker fill of `usd` notional
    walking `levels`. None when the book is too thin to fill it."""
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


def max_usd_within(levels: Levels, mid: float, max_bps: float) -> float:
    """Largest taker notional whose average distance from mid stays within
    `max_bps`. Bounded by the visible depth (thin book -> small size)."""
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


class Book:
    __slots__ = ("asks", "bids", "mid", "source")

    def __init__(self, bids: Levels, asks: Levels, source: str) -> None:
        self.bids, self.asks, self.source = bids, asks, source
        self.mid = (bids[0][0] + asks[0][0]) / 2


def parse_levels(raw) -> Levels:
    return [(float(p), float(q)) for p, q, *_ in raw or [] if float(q) > 0]


def spot_symbol(symbol: str) -> str:
    return f"{base_coin(symbol)}USDT"


def leg_cap_usd(perp: Book, spot: Book, max_bps: float = MAX_LEG_IMPACT_BPS) -> float:
    """Per-leg size at which all four fills (perp buy/sell, spot sell/buy)
    stay within `max_bps` of mid."""
    return min(
        max_usd_within(perp.asks, perp.mid, max_bps),
        max_usd_within(perp.bids, perp.mid, max_bps),
        max_usd_within(spot.bids, spot.mid, max_bps),
        max_usd_within(spot.asks, spot.mid, max_bps),
    )


def round_trip_cost(perp: Book, spot: Book, usd: float) -> dict[str, float] | None:
    """bps of per-leg notional for the whole four-leg round trip, priced on
    today's books (close legs assume the book looks the same at exit)."""
    legs = {
        "perp_buy_bps": walk_bps(perp.asks, perp.mid, usd),
        "perp_sell_bps": walk_bps(perp.bids, perp.mid, usd),
        "spot_sell_bps": walk_bps(spot.bids, spot.mid, usd),
        "spot_buy_bps": walk_bps(spot.asks, spot.mid, usd),
    }
    if any(v is None for v in legs.values()):
        return None
    fees = 2 * PERP_TAKER_BPS + 2 * SPOT_TAKER_BPS
    out = {k: round(v, 3) for k, v in legs.items()}
    out["fees_bps"] = fees
    out["total_bps"] = round(fees + sum(legs.values()), 3)
    return out


_BOOK_URLS = {
    ("bybit", "linear"): "https://api.bybit.com/v5/market/orderbook?category=linear&symbol={s}&limit=200",
    ("bybit", "spot"): "https://api.bybit.com/v5/market/orderbook?category=spot&symbol={s}&limit=200",
    ("binance", "spot"): "https://api.binance.com/api/v3/depth?symbol={s}&limit=100",
}
_DB_EXCHANGE = {("bybit", "linear"): "bybit", ("bybit", "spot"): "bybit-spot", ("binance", "spot"): "binance-spot"}


async def fetch_book(venue: str, category: str, symbol: str, session=None) -> Book | None:
    """Freshest book: an ingested snapshot <= 30 s old, else the venue's public
    REST depth. None when neither exists (no hedge leg -> no trade)."""
    if session is not None:
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
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(_BOOK_URLS[(venue, category)].format(s=symbol))
            r.raise_for_status()
            j = r.json()
    except Exception as e:  # noqa: BLE001 — no book is a skip, not a crash
        logger.debug(f"{STRATEGY_ID}: {venue} {category} book {symbol} unavailable: {e}")
        return None
    if venue == "bybit":
        d = j.get("result") or {}
        b, a = parse_levels(d.get("b")), parse_levels(d.get("a"))
    else:
        b, a = parse_levels(j.get("bids")), parse_levels(j.get("asks"))
    return Book(b, a, "rest") if b and a else None


def interval_hours(settled_at: datetime, next_ts: datetime | None) -> int:
    """Funding interval now in force, from the gap to the next settlement."""
    if next_ts is None:
        return 8
    gap = (next_ts - settled_at).total_seconds() / 3600
    return min(_INTERVALS_H, key=lambda h: abs(h - gap))


def run_length(prior: Sequence[Decimal]) -> int:
    """Consecutive settlements <= RUN_RATE immediately before the signal one
    (`prior` newest first)."""
    n = 0
    for r in prior:
        if r > RUN_RATE:
            break
        n += 1
    return n


def decay_ratio(rate: Decimal, run: int | None) -> float:
    """Train median of realised / naive 48 h funding for this entry. Unknown
    history counts as a fresh spike (run 0), the fastest-decaying bucket."""
    rb = 0 if not run else (1 if run <= 2 else 2)
    db = sum(1 for e in _DEPTH_EDGES if -rate >= e)
    return DECAY_RATIO[(rb, db)]


async def prior_settlements(symbol: str, settled_at: datetime) -> list[Decimal] | None:
    """Rates settled before `settled_at`, newest first (Bybit public history).
    None when the venue does not answer."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(_FUNDING_HISTORY.format(s=symbol))
            r.raise_for_status()
            rows = (r.json().get("result") or {}).get("list") or []
    except Exception as e:  # noqa: BLE001 — no history is "unknown run", not a crash
        logger.debug(f"{STRATEGY_ID}: funding history {symbol} unavailable: {e}")
        return None
    cut = settled_at.timestamp() * 1000 - 1
    pts = sorted(
        ((int(x["fundingRateTimestamp"]), Decimal(x["fundingRate"])) for x in rows
         if int(x["fundingRateTimestamp"]) < cut),
        reverse=True,
    )
    return [r for _, r in pts]


def entry_verdict(
    *, rate: Decimal, interval_h: int, hourly_borrow: Decimal, horizon_h: float,
    cost: dict[str, float] | None, leg_usd: float,
    stress: float = BORROW_STRESS, max_share: float = MAX_COST_SHARE,
    run: int | None = None, model: str = EXPECTED_MODEL,
    decay_max_share: float = DECAY_MAX_COST_SHARE,
) -> tuple[bool, dict]:
    """(take?, the numbers it was decided on). All bps of per-leg notional.
    Gates on the naive expectation (x max_share) unless model == "decay"
    (decayed expectation x decay_max_share); both verdicts are recorded."""
    expected = float(-rate) * 1e4 * horizon_h / interval_h
    ratio = decay_ratio(rate, run)
    decayed = expected * ratio
    borrow = float(hourly_borrow) * 1e4 * horizon_h * stress
    inputs: dict = {
        "interval_h": interval_h,
        "expected_funding_bps": round(expected, 3),
        "expected_model": model,
        "prior_run": run,
        "decay_ratio": ratio,
        "expected_decayed_bps": round(decayed, 3),
        "borrow_quoted_bps": round(float(hourly_borrow) * 1e4 * horizon_h, 3),
        "borrow_stress": stress,
        "borrow_stressed_bps": round(borrow, 3),
        "leg_notional_usd": round(leg_usd, 2),
        "max_cost_share": round(max_share, 4),
    }
    if cost is None or leg_usd < MIN_LEG_USD:
        inputs["skip"] = "book_too_thin"
        return False, inputs
    inputs["book_cost"] = cost
    total = borrow + cost["total_bps"]
    inputs["cost_share"] = round(total / expected, 4) if expected > 0 else None
    inputs["decay_cost_share"] = round(total / decayed, 4) if decayed > 0 else None
    naive_ok = expected > 0 and total <= expected * max_share
    decay_ok = decayed > 0 and total <= decayed * decay_max_share
    inputs["naive_keep"], inputs["decay_keep"] = naive_ok, decay_ok
    if not (decay_ok if model == "decay" else naive_ok):
        inputs["skip"] = "cost_share"
        return False, inputs
    return True, inputs


def base_coin(symbol: str) -> str:
    """BTCUSDT -> BTC, 1000PEPEUSDT -> PEPE."""
    b = symbol.removesuffix("USDT")
    for p in ("1000000", "100000", "10000", "1000"):
        if b.startswith(p) and len(b) > len(p):
            return b[len(p):]
    return b


def parse_borrow(bybit: dict | None, binance: dict | None) -> dict[str, tuple[Decimal, str]]:
    """coin -> (cheapest hourly borrow rate, venue), over venues that lend it."""
    out: dict[str, tuple[Decimal, str]] = {}

    def put(coin: str, rate: Decimal, venue: str) -> None:
        if rate > 0 and (coin not in out or rate < out[coin][0]):
            out[coin] = (rate, venue)

    for group in ((bybit or {}).get("result") or {}).get("vipCoinList") or []:
        for c in group.get("list") or []:
            if c.get("borrowable") and c.get("hourlyBorrowRate"):
                put(c["currency"], Decimal(c["hourlyBorrowRate"]), "bybit")
    for a in (binance or {}).get("data") or []:
        specs = a.get("specs") or []
        vip0 = next((s for s in specs if str(s.get("vipLevel")) == "0"), None)
        if vip0 and vip0.get("dailyInterestRate"):
            put(a["assetName"], Decimal(vip0["dailyInterestRate"]) / 24, "binance")
    return out


async def borrow_rates() -> dict[str, tuple[Decimal, str]]:
    global _borrow_cache
    now = time.monotonic()
    if _borrow_cache is not None and now - _borrow_cache[0] < _BORROW_TTL_S:
        return _borrow_cache[1]
    docs: list[dict | None] = []
    async with httpx.AsyncClient(timeout=15) as client:
        for url in (_BYBIT_MARGIN, _BINANCE_MARGIN):
            try:
                r = await client.get(url)
                r.raise_for_status()
                docs.append(r.json())
            except Exception as e:  # noqa: BLE001 — one venue down leaves the other
                logger.warning(f"{STRATEGY_ID}: borrow table {url.split('/')[2]} unavailable: {e}")
                docs.append(None)
    table = parse_borrow(docs[0], docs[1])
    if table:
        _borrow_cache = (now, table)
    elif _borrow_cache is not None:
        return _borrow_cache[1]  # keep the last good table through an outage
    return table


async def last_settled(session, symbol: str, now: datetime) -> tuple[Decimal, datetime] | None:
    """(rate, settlement time) of the most recent settlement: the last snapshot
    taken within 10 min before a settlement that has already passed, naming it
    as next — the rate in force when it paid (backtest.carry_funding)."""
    row = (
        await session.execute(
            text(
                "SELECT funding_rate, next_funding_ts FROM market_ticker_snapshots "
                "WHERE symbol = :sym AND exchange = 'bybit' AND snapshot_ts > :since "
                "AND next_funding_ts <= :now AND snapshot_ts < next_funding_ts "
                "AND snapshot_ts > next_funding_ts - interval '10 minutes' "
                "AND funding_rate IS NOT NULL ORDER BY snapshot_ts DESC LIMIT 1"
            ),
            {"sym": symbol, "since": now - timedelta(hours=9), "now": now},
        )
    ).first()
    if row is None:
        return None
    return Decimal(row[0]), row[1]


class NegFundingCarry:
    id: str = STRATEGY_ID
    version: int = STRATEGY_VERSION
    market: str = "crypto"

    def __init__(
        self,
        symbols: Sequence[str] | None = None,
        *,
        min_funding: Decimal = DEFAULT_MIN_FUNDING,
        horizon_s: int = DEFAULT_HORIZON_S,
        max_signal_age_s: int = DEFAULT_MAX_SIGNAL_AGE_S,
    ) -> None:
        self.symbols: list[str] = list(symbols) if symbols is not None else crypto_universe()
        self.min_funding = min_funding
        self.horizon_s = horizon_s
        self.max_signal_age_s = max_signal_age_s

    async def generate(self) -> list[PredictionDraft]:
        now = datetime.now(UTC)
        rates = await borrow_rates()
        if not rates:
            return []
        async with shared_session_scope() as shared:
            held = set(
                (
                    await shared.execute(
                        text(
                            "SELECT symbol FROM predictions WHERE strategy_id = :sid "
                            "AND status = 'open' AND close_by > :now"
                        ),
                        {"sid": self.id, "now": now},
                    )
                ).scalars()
            )
        drafts: list[PredictionDraft] = []
        async with local_session_scope() as session:
            for symbol in self.symbols:
                if symbol in held:
                    continue
                borrow = rates.get(base_coin(symbol))
                if borrow is None:
                    continue
                settled = await last_settled(session, symbol, now)
                if settled is None:
                    continue
                rate, settled_at = settled
                if rate > -self.min_funding or (now - settled_at).total_seconds() > self.max_signal_age_s:
                    continue
                last = (
                    await session.execute(
                        text(
                            "SELECT coalesce(mark_price, last_price), next_funding_ts "
                            "FROM market_ticker_snapshots "
                            "WHERE symbol = :sym AND exchange = 'bybit' AND snapshot_ts > :since "
                            "ORDER BY snapshot_ts DESC LIMIT 1"
                        ),
                        {"sym": symbol, "since": now - timedelta(minutes=5)},
                    )
                ).first()
                if last is None or last[0] is None:
                    continue
                px, next_ts = last
                hourly, venue = borrow
                spot_sym = spot_symbol(symbol)
                perp_book = await fetch_book("bybit", "linear", symbol, session)
                spot_book = await fetch_book(venue, "spot", spot_sym, session)
                if perp_book is None or spot_book is None:
                    logger.info(f"{STRATEGY_ID}: skip {symbol}: no {'perp' if perp_book is None else 'spot'} book")
                    continue
                leg_cap = leg_cap_usd(perp_book, spot_book)
                leg_usd = min(leg_cap, MAX_LEG_USD)
                cost = round_trip_cost(perp_book, spot_book, leg_usd) if leg_usd > 0 else None
                prior = await prior_settlements(symbol, settled_at)
                ok, inputs = entry_verdict(
                    rate=rate, interval_h=interval_hours(settled_at, next_ts),
                    hourly_borrow=hourly, horizon_h=self.horizon_s / 3600,
                    cost=cost, leg_usd=leg_usd,
                    run=run_length(prior) if prior is not None else None,
                )
                if not ok:
                    logger.info(
                        f"{STRATEGY_ID}: skip {symbol} ({inputs['skip']}): expected "
                        f"{inputs['expected_funding_bps']:.0f} bps, borrow x{BORROW_STRESS:g} "
                        f"{inputs['borrow_stressed_bps']:.0f}, book "
                        f"{(cost or {}).get('total_bps', float('nan')):.0f} at ${leg_usd:.0f}/leg"
                    )
                    continue
                mag = -rate
                conf = max(min(mag / FUNDING_CAP, Decimal("1")), Decimal("0.10"))
                drafts.append(
                    PredictionDraft(
                        strategy_id=self.id,
                        strategy_version=self.version,
                        symbol=symbol,
                        exchange="bybit",
                        side="inverse_carry",
                        confidence=conf,
                        horizon_seconds=self.horizon_s,
                        entry_price_ref=Decimal(px),
                        generated_at=now,
                        thesis=(
                            f"settled funding {rate * 100:.4f}% at {settled_at:%H:%M}Z; short {base_coin(symbol)} "
                            f"spot (borrow {hourly * 80000:.2f} bps/8h on {venue}) / long perp for 48h; "
                            f"expected {inputs['expected_funding_bps']:.0f} bps vs borrow x{BORROW_STRESS:g} "
                            f"{inputs['borrow_stressed_bps']:.0f} + book {cost['total_bps']:.0f} at ${leg_usd:.0f}/leg"
                        ),
                        context={
                            "funding_rate_8h": str(rate),
                            "settled_at": settled_at.isoformat(),
                            "borrow_rate_hourly": str(hourly),
                            "borrow_venue": venue,
                            "spot_venue": venue,
                            "spot_symbol": spot_sym,
                            "leg_cap_usd": round(leg_cap, 2),
                            "book_sources": {"perp": perp_book.source, "spot": spot_book.source},
                            "entry_filter": inputs,
                        },
                    )
                )
        return drafts

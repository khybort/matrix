"""Carry watchlist: stream the coins neg_funding_carry could trade next.

The hedged negative-funding carry (docs/wiki/signal-research-2026-10.md)
earns mostly OUTSIDE the 25-symbol live universe: 19 of 1 105 holdout
episodes fell inside it. A coin nothing streams has no ticker, so the
strategy cannot see its settlement and the stale-data guard would drop it
anyway. This module picks the coins worth streaming and writes them to
`tradable_symbols` under asset_class `crypto_carry` (no migration: the
table already carries active/score/rank/components per asset class, and the
crypto universe manager only touches `crypto`). Ingestion and the bars
aggregator stream `crypto_ingest_universe_async()` = active ∪ watchlist.

Selection, hourly at minute REFRESH_MINUTE (default :40, so a newcomer is
subscribed ~20 min before the top-of-hour settlement the strategy reads):
  * Bybit USDT perp whose base coin is in the borrow tables the strategy
    uses (Bybit spot-margin, Binance cross-margin VIP0) — no borrow, no trade.
  * min(last settled, live predicted) funding <= ENTER (-0.05 %): a margin
    below the -0.08 % entry, and the predicted rate gets the coin warm
    BEFORE the settlement that would trigger, not after it.
  * Hysteresis: a member stays while that rate is <= EXIT (-0.02 %) or it
    qualified within KEEP_H hours; an incumbent's turnover counts 1.5x in the
    cap ranking, so two coins near rank N do not swap every hour.
  * Pinned: any symbol with an open `inverse_carry` prediction stays, cap or
    not — the paper engine accrues funding from the settlements the hold
    crosses (backtest.carry_funding), which needs the ticker stream.
  * Capped at N (default 20) by 24h turnover: liquid coins earned more
    (> $20M/day: +420 bps per episode).

Each member also names its spot leg: the spot pair on the cheapest-borrow
venue when listed there, else the other venue's.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import orjson
from loguru import logger
from sqlalchemy import text

from matrix_shared import shared_session_scope
from matrix_shared.markets.crypto import CARRY_WATCHLIST_ASSET_CLASS, crypto_universe_async

ENABLED = os.environ.get("CARRY_WATCHLIST_ENABLED", "true").strip().lower() != "false"
CAP_N = int(os.environ.get("CARRY_WATCHLIST_N", "20"))
ENTER = Decimal(os.environ.get("CARRY_WATCHLIST_ENTER", "-0.0005"))
EXIT = Decimal(os.environ.get("CARRY_WATCHLIST_EXIT", "-0.0002"))
KEEP_H = float(os.environ.get("CARRY_WATCHLIST_KEEP_H", "6"))
REFRESH_MINUTE = int(os.environ.get("CARRY_WATCHLIST_REFRESH_MINUTE", "40"))
INCUMBENT_BONUS = 1.5

_BYBIT = "https://api.bybit.com"
# Same public tables strategy.modules.crypto.neg_funding_carry reads.
_BYBIT_MARGIN = f"{_BYBIT}/v5/spot-margin-trade/data?vipLevel=No%20VIP"
_BINANCE_MARGIN = "https://www.binance.com/bapi/margin/v1/public/margin/vip/spec/list-all"
_BINANCE_SPOT_INFO = "https://api.binance.com/api/v3/exchangeInfo"


def base_coin(symbol: str) -> str:
    """BTCUSDT -> BTC, 1000PEPEUSDT -> PEPE (mirrors neg_funding_carry.base_coin)."""
    b = symbol.removesuffix("USDT")
    for p in ("1000000", "100000", "10000", "1000"):
        if b.startswith(p) and len(b) > len(p):
            return b[len(p):]
    return b


def parse_borrow(bybit: dict | None, binance: dict | None) -> dict[str, tuple[Decimal, str]]:
    """coin -> (cheapest hourly borrow rate, venue); mirrors neg_funding_carry.parse_borrow."""
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


@dataclass(slots=True)
class Candidate:
    symbol: str
    turnover: float
    settled: Decimal | None
    predicted: Decimal | None
    borrow_venue: str | None = None
    borrow_hourly: Decimal | None = None

    @property
    def rate(self) -> Decimal | None:
        vals = [v for v in (self.settled, self.predicted) if v is not None]
        return min(vals) if vals else None


@dataclass(slots=True)
class Member:
    symbol: str
    rank: int
    pinned: bool
    last_qualified_at: datetime | None


def select_watchlist(
    candidates: Iterable[Candidate],
    incumbents: dict[str, datetime | None],
    pinned: set[str],
    core: set[str],
    now: datetime,
    *,
    n: int = CAP_N,
    enter: Decimal = ENTER,
    exit_: Decimal = EXIT,
    keep_h: float = KEEP_H,
) -> list[Member]:
    """Pure selection. `candidates` are borrowable perps; `incumbents` maps the
    current members to when they last met `enter`; `core` (the live universe)
    is streamed already and never takes a slot."""
    by_sym = {c.symbol: c for c in candidates}
    out: list[Member] = []
    for sym in sorted(pinned - core):
        c = by_sym.get(sym)
        q = c is not None and c.rate is not None and c.rate <= enter
        out.append(Member(sym, 0, True, now if q else incumbents.get(sym)))
    taken = {m.symbol for m in out}
    entering: list[tuple[float, Candidate]] = []
    staying: list[tuple[float, Candidate, datetime | None]] = []
    for c in by_sym.values():
        if c.symbol in core or c.symbol in taken or c.rate is None:
            continue
        inc = c.symbol in incumbents
        weight = c.turnover * (INCUMBENT_BONUS if inc else 1.0)
        if c.rate <= enter:
            entering.append((weight, c))
        elif inc:
            last = incumbents[c.symbol]
            recent = last is not None and now - last <= timedelta(hours=keep_h)
            if c.rate <= exit_ or recent:
                staying.append((weight, c, last))
    entering.sort(key=lambda t: -t[0])
    staying.sort(key=lambda t: -t[0])
    free = max(0, n - len(out))
    for _, c in entering[:free]:
        out.append(Member(c.symbol, 0, False, now))
    free = max(0, n - len(out))
    for _, c, last in staying[:free]:
        out.append(Member(c.symbol, 0, False, last))
    for i, m in enumerate(out, 1):
        m.rank = i
    return out


def spot_leg(symbol: str, borrow_venue: str | None, bybit_spot: set[str], binance_spot: set[str]) -> tuple[str, str] | None:
    """(venue, spot pair) for the hedge: the cheapest-borrow venue if it lists
    the pair, else the other venue."""
    pair = f"{base_coin(symbol)}USDT"
    listed = {"bybit": pair in bybit_spot, "binance": pair in binance_spot}
    order = ["binance", "bybit"] if borrow_venue == "binance" else ["bybit", "binance"]
    for v in order:
        if listed[v]:
            return v, pair
    return None


async def _get(client: httpx.AsyncClient, url: str, **params) -> dict | None:
    try:
        r = await client.get(url, params=params or None)
        r.raise_for_status()
        return r.json()
    except Exception as e:  # noqa: BLE001 — one source down skips that source
        logger.warning(f"carry watchlist: {url.split('?')[0]} unavailable: {e}")
        return None


async def _settled(client: httpx.AsyncClient, symbols: list[str]) -> dict[str, Decimal]:
    sem = asyncio.Semaphore(10)  # 363 calls in ~8 s, far under Bybit's 600/5 s

    async def one(sym: str) -> tuple[str, Decimal | None]:
        async with sem:
            doc = await _get(client, f"{_BYBIT}/v5/market/funding/history",
                             category="linear", symbol=sym, limit=1)
        rows = ((doc or {}).get("result") or {}).get("list") or []
        return sym, Decimal(rows[0]["fundingRate"]) if rows else None

    return {s: r for s, r in await asyncio.gather(*(one(s) for s in symbols)) if r is not None}


async def _incumbents() -> tuple[dict[str, datetime | None], set[str]]:
    async with shared_session_scope() as db:
        rows = (await db.execute(text(
            "SELECT symbol, components_json FROM tradable_symbols "
            "WHERE asset_class = :ac AND active"), {"ac": CARRY_WATCHLIST_ASSET_CLASS})).all()
        pinned = set((await db.execute(text(
            "SELECT DISTINCT symbol FROM predictions WHERE status = 'open' "
            "AND side = 'inverse_carry' AND asset_class = 'crypto'"))).scalars())
    inc: dict[str, datetime | None] = {}
    for sym, comp in rows:
        last = (comp or {}).get("last_qualified_at")
        inc[sym] = datetime.fromisoformat(last) if last else None
    return inc, pinned


async def refresh(now: datetime | None = None) -> list[str]:
    """Recompute and persist the watchlist. Returns the active symbols; on a
    failed exchange fetch the previous list stands (nothing is written)."""
    now = now or datetime.now(UTC)
    async with httpx.AsyncClient(timeout=20) as client:
        tick = await _get(client, f"{_BYBIT}/v5/market/tickers", category="linear")
        bybit_m = await _get(client, _BYBIT_MARGIN)
        binance_m = await _get(client, _BINANCE_MARGIN)
        spot_b = await _get(client, f"{_BYBIT}/v5/market/instruments-info", category="spot", limit=1000)
        spot_x = await _get(client, _BINANCE_SPOT_INFO, permissions="SPOT")
        tickers = ((tick or {}).get("result") or {}).get("list") or []
        borrow = parse_borrow(bybit_m, binance_m)
        if not tickers or not borrow:
            logger.warning("carry watchlist: tickers or borrow tables empty; keeping the current list")
            return []
        perps = [t for t in tickers if t.get("symbol", "").endswith("USDT") and base_coin(t["symbol"]) in borrow]
        settled = await _settled(client, [t["symbol"] for t in perps])
    bybit_spot = {s["symbol"] for s in ((spot_b or {}).get("result") or {}).get("list") or []
                  if s.get("quoteCoin") == "USDT" and s.get("status") == "Trading"}
    binance_spot = {s["symbol"] for s in (spot_x or {}).get("symbols") or []
                    if s.get("quoteAsset") == "USDT" and s.get("status") == "TRADING"}
    cands: list[Candidate] = []
    for t in perps:
        hourly, venue = borrow[base_coin(t["symbol"])]
        cands.append(Candidate(
            symbol=t["symbol"],
            turnover=float(t.get("turnover24h") or 0),
            settled=settled.get(t["symbol"]),
            predicted=Decimal(t["fundingRate"]) if t.get("fundingRate") else None,
            borrow_venue=venue,
            borrow_hourly=hourly,
        ))
    incumbents, pinned = await _incumbents()
    core = set(await crypto_universe_async())
    members = select_watchlist(cands, incumbents, pinned, core, now)
    by_sym = {c.symbol: c for c in cands}
    rows = []
    for m in members:
        c = by_sym.get(m.symbol)
        leg = spot_leg(m.symbol, c.borrow_venue if c else None, bybit_spot, binance_spot)
        rows.append({
            "ac": CARRY_WATCHLIST_ASSET_CLASS, "symbol": m.symbol, "rank": m.rank,
            "score": float(c.rate) if c and c.rate is not None else None,
            "liq": c.turnover if c else None,
            "comp": orjson.dumps({
                "settled": str(c.settled) if c and c.settled is not None else None,
                "predicted": str(c.predicted) if c and c.predicted is not None else None,
                "last_qualified_at": m.last_qualified_at.isoformat() if m.last_qualified_at else None,
                "pinned": m.pinned,
                "borrow_venue": c.borrow_venue if c else None,
                "borrow_hourly": str(c.borrow_hourly) if c and c.borrow_hourly is not None else None,
                "spot_venue": leg[0] if leg else None,
                "spot_symbol": leg[1] if leg else None,
            }).decode(),
            "now": now,
        })
    keep = [r["symbol"] for r in rows]
    async with shared_session_scope() as db:
        for r in rows:
            await db.execute(text(
                "INSERT INTO tradable_symbols (asset_class, symbol, active, score, components_json, "
                "liquidity_usd, rank, became_active_at, last_scored_at) "
                "VALUES (:ac, :symbol, true, :score, CAST(:comp AS json), :liq, :rank, :now, :now) "
                "ON CONFLICT (asset_class, symbol) DO UPDATE SET active = true, score = EXCLUDED.score, "
                "components_json = EXCLUDED.components_json, liquidity_usd = EXCLUDED.liquidity_usd, "
                "rank = EXCLUDED.rank, last_scored_at = EXCLUDED.last_scored_at, updated_at = now(), "
                "became_active_at = CASE WHEN tradable_symbols.active "
                "THEN tradable_symbols.became_active_at ELSE EXCLUDED.became_active_at END"), r)
        await db.execute(text(
            "UPDATE tradable_symbols SET active = false, rank = NULL, updated_at = now() "
            "WHERE asset_class = :ac AND active AND NOT (symbol = ANY(:keep))"),
            {"ac": CARRY_WATCHLIST_ASSET_CLASS, "keep": keep})
    added = sorted(set(keep) - set(incumbents))
    dropped = sorted(set(incumbents) - set(keep))
    logger.info(
        f"carry watchlist: {len(keep)} active (borrowable perps={len(perps)}, "
        f"settled fetched={len(settled)}) added={added} dropped={dropped}"
    )
    return keep


def _seconds_to_next(now: datetime, minute: int) -> float:
    nxt = now.replace(minute=minute, second=0, microsecond=0)
    if nxt <= now:
        nxt += timedelta(hours=1)
    return (nxt - now).total_seconds()


async def run(changed: asyncio.Event) -> None:
    """Refresh on start, then hourly at REFRESH_MINUTE; set `changed` after
    each write so the ingestor resubscribes at once instead of on its timer."""
    while True:
        try:
            await refresh()
            changed.set()
        except Exception as e:  # noqa: BLE001 — never take the market stream down
            logger.exception(f"carry watchlist refresh failed: {e}")
        await asyncio.sleep(_seconds_to_next(datetime.now(UTC), REFRESH_MINUTE))

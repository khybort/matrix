"""Two-leg carry execution: borrow, short spot, long perp, and back.

The execution service only knew single-leg directional orders. A spot-hedged
carry (`neg_funding_carry`: long the Bybit perp, short the coin on Bybit spot
margin or Binance cross margin, 48 h) is three venue actions that must end
hedged or flat, never half-done. Design: docs/wiki/carry-execution.md.

Modes (`MATRIX_CARRY_EXEC_MODE`):
- `dry_run` (default): every request is built and signed exactly as it would
  be sent (with a placeholder key, against the mainnet hosts live would use),
  logged, and never sent: the transport is `NeverSend` whatever the caller
  passes. Fills are simulated by walking the live DB books with the same
  bounded-slippage IOC prices a live order would carry. The gate is still
  evaluated and recorded; a refusal does not stop the simulation, because
  measuring the executable path is the point.
- `testnet`: sends to Bybit's testnet only (`carry_venues.SENDABLE_HOSTS`),
  and only when `matrix_shared.live_gate.should_submit_live` allows the
  combined notional of both legs. Binance has no margin testnet, so a carry
  borrowing on Binance is refused in this mode.
There is no mainnet mode. Phase 5 adds one by a human commit.

Order of legs. Open: borrow -> sell spot -> buy perp -> repay any excess
borrow. Close: buy spot -> sell perp -> repay. Both directions run the thin,
uncertain spot leg first: if it fails nothing is exposed (open) or the hedge
is still intact (close). A failed second leg is never left naked: on open the
first leg is unwound at once with a bounded-slippage IOC (two widening
attempts); on close the perp sell is retried the same way. If that fails too
the executor alerts and trips the kill switch (process latch + the wallet's
daily-loss circuit, which the gate already honours). Dry-run only records
`would_kill`.
"""

from __future__ import annotations

import asyncio
import math
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any, Protocol
from uuid import UUID

import httpx
from loguru import logger
from sqlalchemy import text

from matrix_shared.carry_venues import (
    BINANCE_MAINNET,
    BYBIT_MAINNET,
    BYBIT_TESTNET,
    DRY_RUN_KEY,
    DRY_RUN_SECRET,
    TESTNET_MARGIN,
    BinanceMarginRequests,
    BybitV5Requests,
    HttpTransport,
    NeverSend,
    SignedRequest,
    Transport,
)
from matrix_shared.db import local_session_scope, shared_session_scope
from matrix_shared.live_gate import should_submit_live
from matrix_shared.models import Wallet
from matrix_shared.rate_limiter import TokenBucket

DRY_RUN = "dry_run"
TESTNET = "testnet"
MODES = (DRY_RUN, TESTNET)


def _f(name: str, default: str) -> float:
    return float(os.environ.get(name) or default)


BAND_BPS = _f("MATRIX_CARRY_EXEC_BAND_BPS", "30")  # IOC price band of a normal leg
UNWIND_BAND_BPS = _f("MATRIX_CARRY_EXEC_UNWIND_BAND_BPS", "100")  # first unwind attempt; second is 2x
MAX_OPENS_PER_HOUR = _f("MATRIX_CARRY_EXEC_MAX_OPENS_PER_HOUR", "6")
UNCONFIRMED_MAX_LEG_USD = Decimal(os.environ.get("MATRIX_CARRY_UNCONFIRMED_MAX_LEG_USD") or "500")
MIN_LEG_USD = Decimal(os.environ.get("MATRIX_CARRY_MIN_LEG_USD") or "50")
BORROW_DRIFT_MAX = _f("MATRIX_CARRY_EXEC_BORROW_DRIFT_MAX", "1.5")  # quote now / quote at signal
MARGIN_BUFFER = Decimal(os.environ.get("MATRIX_CARRY_EXEC_MARGIN_BUFFER") or "1.0")
PERP_TAKER_BPS = _f("MATRIX_CARRY_PERP_TAKER_BPS", "5.5")
SPOT_TAKER_BPS = _f("MATRIX_CARRY_SPOT_TAKER_BPS", "10")
BOOK_MAX_AGE_S = _f("MATRIX_CARRY_EXEC_BOOK_MAX_AGE_S", "60")
REQ_PER_SEC = _f("MATRIX_CARRY_EXEC_REQ_PER_SEC", "5")
MIRROR_TIMEOUT_S = _f("MATRIX_CARRY_MIRROR_TIMEOUT_S", "5")


def resolve_mode(mode: str | None = None) -> str:
    m = (mode or os.environ.get("MATRIX_CARRY_EXEC_MODE") or DRY_RUN).strip().lower()
    if m not in MODES:
        logger.warning(f"carry_exec: unknown mode {m!r}; using dry_run")
        return DRY_RUN
    return m


# ---------------------------------------------------------------- market data

Levels = list[tuple[float, float]]


@dataclass(slots=True)
class Book:
    bids: Levels
    asks: Levels
    source: str = "db"

    @property
    def mid(self) -> float:
        return (self.bids[0][0] + self.asks[0][0]) / 2


@dataclass(frozen=True, slots=True)
class InstrumentSpec:
    qty_step: Decimal
    min_qty: Decimal
    tick: Decimal
    min_notional: Decimal = Decimal("5")
    source: str = "rest"


FALLBACK_SPEC = InstrumentSpec(Decimal("0.00000001"), Decimal("0"), Decimal("0.00000001"), Decimal("5"), "fallback")


def floor_step(x: Decimal, step: Decimal) -> Decimal:
    return (x / step).to_integral_value(ROUND_FLOOR) * step if step > 0 else x


def ceil_step(x: Decimal, step: Decimal) -> Decimal:
    return (x / step).to_integral_value(ROUND_CEILING) * step if step > 0 else x


def dstr(x: Decimal) -> str:
    return format(x.normalize(), "f")


def _parse_levels(raw) -> Levels:
    return [(float(p), float(q)) for p, q, *_ in raw or [] if float(q) > 0]


_DB_EXCHANGE = {("bybit", "linear"): "bybit", ("bybit", "spot"): "bybit-spot", ("binance", "spot"): "binance-spot"}


async def db_book(venue: str, category: str, symbol: str) -> Book | None:
    """Latest ingested book <= BOOK_MAX_AGE_S old. DB only: the paper loop
    calls the mirror inline and must never wait on REST."""
    ex = _DB_EXCHANGE.get((venue, category))
    if ex is None:
        return None
    async with local_session_scope() as session:
        row = (await session.execute(
            text("SELECT bids, asks FROM market_orderbook_snapshots WHERE exchange = :ex AND symbol = :s "
                 "AND snapshot_ts > :since ORDER BY snapshot_ts DESC LIMIT 1"),
            {"ex": ex, "s": symbol, "since": datetime.now(UTC) - timedelta(seconds=BOOK_MAX_AGE_S)},
        )).first()
    if row is None:
        return None
    b, a = _parse_levels(row[0]), _parse_levels(row[1])
    return Book(b, a, "db") if b and a else None


_SPEC_CACHE: dict[tuple[str, str, str], tuple[float, InstrumentSpec]] = {}
SPEC_TTL_S = 6 * 3600


async def rest_spec(venue: str, category: str, symbol: str) -> InstrumentSpec | None:
    """Lot/tick filters from the venue's public instruments endpoint, cached 6 h."""
    key = (venue, category, symbol)
    hit = _SPEC_CACHE.get(key)
    if hit and time.monotonic() - hit[0] < SPEC_TTL_S:
        return hit[1]
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            if venue == "bybit":
                r = await client.get(f"{BYBIT_MAINNET}/v5/market/instruments-info",
                                     params={"category": category, "symbol": symbol})
                it = r.json()["result"]["list"][0]
                lot, pf = it["lotSizeFilter"], it["priceFilter"]
                step = lot.get("qtyStep") or lot.get("basePrecision")
                spec = InstrumentSpec(Decimal(step), Decimal(lot["minOrderQty"]), Decimal(pf["tickSize"]),
                                      Decimal(lot.get("minNotionalValue") or lot.get("minOrderAmt") or "5"))
            else:
                r = await client.get(f"{BINANCE_MAINNET}/api/v3/exchangeInfo", params={"symbol": symbol})
                flt = {f["filterType"]: f for f in r.json()["symbols"][0]["filters"]}
                spec = InstrumentSpec(Decimal(flt["LOT_SIZE"]["stepSize"]), Decimal(flt["LOT_SIZE"]["minQty"]),
                                      Decimal(flt["PRICE_FILTER"]["tickSize"]),
                                      Decimal((flt.get("NOTIONAL") or flt.get("MIN_NOTIONAL") or {}).get("minNotional", "5")))
    except Exception as e:  # noqa: BLE001 — external API; a missing spec is handled by the caller
        logger.debug(f"carry_exec: no instrument spec for {venue}/{category}/{symbol}: {e}")
        return None
    _SPEC_CACHE[key] = (time.monotonic(), spec)
    return spec


@dataclass(frozen=True, slots=True)
class BorrowQuote:
    hourly: Decimal
    max_borrow: Decimal | None
    borrowable: bool
    ts: datetime | None = None


async def db_borrow_quote(venue: str, coin: str) -> BorrowQuote | None:
    """Latest recorded public quote (ingestion.borrow_recorder, margin_borrow_rates)."""
    try:
        async with local_session_scope() as session:
            row = (await session.execute(
                text("SELECT hourly_rate, max_borrow, borrowable, ts FROM margin_borrow_rates "
                     "WHERE venue = :v AND coin = :c ORDER BY ts DESC LIMIT 1"),
                {"v": venue, "c": coin},
            )).first()
    except Exception as e:  # noqa: BLE001 — no table on a fresh node = no quote
        logger.debug(f"carry_exec: borrow quote {venue}/{coin} unavailable: {e}")
        return None
    if row is None:
        return None
    return BorrowQuote(Decimal(row[0]), Decimal(row[1]) if row[1] is not None else None, bool(row[2]), row[3])


# --------------------------------------------------------------- persistence

class CarryStore(Protocol):
    async def load(self, prediction_id: UUID, mode: str, action: str) -> dict | None: ...
    async def save(self, prediction_id: UUID, mode: str, action: str, record: dict) -> None: ...


class PredictionContextStore:
    """Executions live in `predictions.context["carry_exec_<mode>"][action]`,
    written in one statement (no read-modify-write race with the engine)."""

    async def load(self, prediction_id: UUID, mode: str, action: str) -> dict | None:
        async with shared_session_scope() as session:
            row = (await session.execute(
                text("SELECT context::jsonb -> :k -> :a FROM predictions WHERE id = :id"),
                {"k": f"carry_exec_{mode}", "a": action, "id": prediction_id},
            )).first()
        return row[0] if row and row[0] else None

    async def save(self, prediction_id: UUID, mode: str, action: str, record: dict) -> None:
        import json

        async with shared_session_scope() as session:
            await session.execute(
                text("UPDATE predictions SET context = (COALESCE(context::jsonb, '{}'::jsonb) || "
                     "jsonb_build_object(CAST(:k AS text), COALESCE(context::jsonb -> :k, '{}'::jsonb) || "
                     "jsonb_build_object(CAST(:a AS text), CAST(:rec AS jsonb))))::json WHERE id = :id"),
                {"k": f"carry_exec_{mode}", "a": action, "rec": json.dumps(record, default=str),
                 "id": prediction_id},
            )


class MemoryStore:
    def __init__(self) -> None:
        self.rows: dict[tuple[UUID, str, str], dict] = {}

    async def load(self, prediction_id: UUID, mode: str, action: str) -> dict | None:
        return self.rows.get((prediction_id, mode, action))

    async def save(self, prediction_id: UUID, mode: str, action: str, record: dict) -> None:
        self.rows[(prediction_id, mode, action)] = record


# ---------------------------------------------------------------- data shapes

@dataclass(slots=True)
class CarryIntent:
    prediction_id: UUID
    strategy_id: str
    strategy_version: int
    wallet_id: UUID
    perp_symbol: str
    spot_venue: str  # bybit | binance (also the borrow venue)
    spot_symbol: str
    leg_usd: Decimal  # requested per-leg notional (the paper size)
    asset_class: str = "crypto"
    confirmed: bool = False  # edge_study promotion status == confirmed
    borrow_hourly: Decimal | None = None  # quote the signal priced
    paper: dict | None = None  # paper walks (book_open / book_close) for the gap

    @property
    def coin(self) -> str:
        return self.spot_symbol.removesuffix("USDT")


@dataclass(slots=True)
class LegFill:
    leg: str
    venue: str
    symbol: str
    side: str  # Buy | Sell
    qty: Decimal
    filled: Decimal = Decimal("0")
    avg_price: float | None = None
    mid: float | None = None
    fee_usd: float = 0.0
    status: str = "unfilled"  # filled | partial | unfilled | rejected | error
    link_id: str = ""
    detail: str = ""

    @property
    def slippage_bps(self) -> float | None:
        if self.avg_price is None or not self.mid:
            return None
        s = (self.avg_price - self.mid) / self.mid * 1e4
        return round(s if self.side == "Buy" else -s, 3)


@dataclass(slots=True)
class CarryResult:
    action: str
    mode: str
    intent: CarryIntent
    status: str = "pending"  # done | partial | refused | aborted | unwound | naked
    reasons: list[str] = field(default_factory=list)
    gate: dict = field(default_factory=dict)
    qty: Decimal = Decimal("0")  # hedged qty after the action (open) / closed qty (close)
    leg_usd: float = 0.0
    combined_usd: float = 0.0
    legs: list[LegFill] = field(default_factory=list)
    requests: list[dict] = field(default_factory=list)
    reconcile: dict = field(default_factory=dict)
    alerts: list[str] = field(default_factory=list)
    would_kill: bool = False
    sent: int = 0
    step: Decimal = Decimal("0")  # common qty step of both legs

    def finish(self, status: str, reason: str | None = None) -> CarryResult:
        self.status = status
        if reason:
            self.reasons.append(reason)
        return self

    def gap_bps(self) -> dict[str, float]:
        """Executable minus paper slippage, per main leg (bps of notional)."""
        paper = self.intent.paper or {}
        out = {}
        for f in self.legs:
            ref = paper.get(f"{f.leg}_bps")
            if ref is not None and f.slippage_bps is not None:
                out[f.leg] = round(f.slippage_bps - float(ref), 3)
        return out

    def record(self) -> dict:
        return {
            "status": self.status, "mode": self.mode, "qty": dstr(self.qty), "leg_usd": round(self.leg_usd, 2),
            "combined_usd": round(self.combined_usd, 2), "reasons": self.reasons,
            "gate": {"allowed": self.gate.get("allowed"), "reasons": self.gate.get("reasons", [])},
            "legs": [{"leg": f.leg, "venue": f.venue, "side": f.side, "qty": dstr(f.qty), "filled": dstr(f.filled),
                      "avg": f.avg_price, "mid": f.mid, "slip_bps": f.slippage_bps, "status": f.status,
                      "link": f.link_id} for f in self.legs],
            "gap_bps": self.gap_bps(), "reconcile": self.reconcile, "would_kill": self.would_kill,
            "n_requests": len(self.requests), "sent": self.sent, "ts": datetime.now(UTC).isoformat(),
        }

    def log_line(self) -> str:
        it = self.intent
        g = "allowed" if self.gate.get("allowed") else f"refused({len(self.gate.get('reasons', []))})"
        paper = it.paper or {}
        legs = " ".join(
            f"{f.leg}[{f.venue}] {dstr(f.filled)}/{dstr(f.qty)}@{f.avg_price:.6g} slip={f.slippage_bps:+.1f}bps"
            + (f" paper={float(paper[f.leg + '_bps']):+.1f}" if paper.get(f.leg + "_bps") is not None else "")
            if f.avg_price is not None else f"{f.leg}[{f.venue}] {f.status}"
            for f in self.legs
        )
        gaps = self.gap_bps()
        return (
            f"carry_exec[{self.mode}] {self.action} {it.perp_symbol}/{it.spot_venue} pred={it.prediction_id} "
            f"status={self.status} gate={g} qty={dstr(self.qty)} leg=${self.leg_usd:.2f} "
            f"combined=${self.combined_usd:.2f} {legs} gap={sum(gaps.values()):+.1f}bps "
            f"reqs={len(self.requests)} sent={self.sent}"
            + (f" reasons={self.reasons}" if self.reasons else "")
            + (" WOULD_KILL" if self.would_kill else "")
        )


AlertFn = Callable[[str, str], Awaitable[None]]


async def _telegram(text_: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chats = [c.strip() for c in os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", "").split(",") if c.strip()]
    if not token or not chats:
        return
    async with httpx.AsyncClient(timeout=10.0) as client:
        for chat in chats:
            try:
                await client.post(f"https://api.telegram.org/bot{token}/sendMessage",
                                  json={"chat_id": chat, "text": text_})
            except Exception as e:  # noqa: BLE001
                logger.warning(f"carry_exec: telegram alert failed: {type(e).__name__}")


# Process-wide kill latch (testnet/live only). Reset only by an operator call.
_KILL: dict[str, Any] = {"halted": False, "reason": None, "at": None}


def kill_switch_state() -> dict[str, Any]:
    return dict(_KILL)


def kill_switch_reset() -> None:
    _KILL.update(halted=False, reason=None, at=None)


# ---------------------------------------------------------------- venue ops

def _link(it: CarryIntent, action: str, leg: str, attempt: int) -> str:
    """Deterministic client order id (<= 36 chars): the same prediction, action,
    leg and attempt can never become two orders; a retry after a timeout
    finds the first one by this id instead."""
    return f"nfc{it.prediction_id.hex[:16]}{action[0]}{leg[:2]}{leg.split('_')[1][0]}{attempt}"


class _Ops:
    """One venue action set; `_SimOps` and `_LiveOps` share every request
    builder, so dry-run builds exactly what testnet/live sends."""

    def __init__(self, ex: CarryExecutor, res: CarryResult, bybit: BybitV5Requests,
                 binance: BinanceMarginRequests) -> None:
        self.ex, self.res, self.bybit, self.binance = ex, res, bybit, binance

    def _req(self, req: SignedRequest) -> SignedRequest:
        self.res.requests.append(req.redacted())
        logger.debug(f"carry_exec[{self.res.mode}] request {req.redacted()}")
        return req

    def borrow_check_req(self, it: CarryIntent) -> SignedRequest:
        return self._req(self.bybit.borrow_check(it.spot_symbol) if it.spot_venue == "bybit"
                         else self.binance.max_borrowable(it.coin))

    def borrow_req(self, it: CarryIntent, qty: Decimal) -> SignedRequest:
        return self._req(self.bybit.borrow(it.coin, dstr(qty)) if it.spot_venue == "bybit"
                         else self.binance.borrow(it.coin, dstr(qty)))

    def repay_req(self, it: CarryIntent, qty: Decimal) -> SignedRequest:
        return self._req(self.bybit.repay(it.coin, dstr(qty)) if it.spot_venue == "bybit"
                         else self.binance.repay(it.coin, dstr(qty)))

    def order_req(self, venue: str, category: str, symbol: str, side: str, qty: Decimal, price: Decimal,
                  link: str, reduce_only: bool) -> SignedRequest:
        if venue == "bybit":
            return self._req(self.bybit.order(category=category, symbol=symbol, side=side, qty=dstr(qty),
                                              price=dstr(price), link_id=link, reduce_only=reduce_only))
        return self._req(self.binance.order(symbol=symbol, side=side, qty=dstr(qty), price=dstr(price), link_id=link))


class _SimOps(_Ops):
    """Dry-run: build every request, send none, fill on the DB books."""

    def __init__(self, *a, books: dict[str, Book], quote: BorrowQuote | None) -> None:
        super().__init__(*a)
        self.books, self.quote = books, quote
        self.used: dict[tuple[str, str], float] = {}

    async def borrow_check(self, it: CarryIntent, qty: Decimal) -> tuple[bool, str]:
        self.borrow_check_req(it)
        q = self.quote
        if q is None:
            return True, "no recorded quote (not simulated)"
        if not q.borrowable:
            return False, f"{it.coin} not borrowable on {it.spot_venue}"
        if q.max_borrow is not None and q.max_borrow < qty:
            return False, f"max_borrow {dstr(q.max_borrow)} < {dstr(qty)}"
        return True, f"max_borrow {dstr(q.max_borrow) if q.max_borrow is not None else '?'}"

    async def margin_check(self, it: CarryIntent, need_usd: Decimal) -> tuple[bool, str]:
        self._req(self.bybit.wallet())
        return True, "not simulated (no account in dry-run)"

    async def borrow(self, it: CarryIntent, qty: Decimal) -> tuple[bool, str]:
        self.borrow_req(it, qty)
        return True, "simulated"

    async def repay(self, it: CarryIntent, qty: Decimal) -> tuple[bool, str]:
        self.repay_req(it, qty)
        return True, "simulated"

    async def order(self, f: LegFill, category: str, price: Decimal, reduce_only: bool = False) -> LegFill:
        self.order_req(f.venue, category, f.symbol, f.side, f.qty, price, f.link_id, reduce_only)
        name = "perp" if category == "linear" else "spot"
        book = self.books[name]
        levels = book.asks if f.side == "Buy" else book.bids
        lim = float(price)
        # Depth taken by an earlier attempt on the same side is gone: a retry
        # walks what is left, it does not refill from the top.
        skip = self.used.get((name, f.side), 0.0)
        rem, got, cost = float(f.qty), 0.0, 0.0
        for p, q in levels:
            if (f.side == "Buy" and p > lim) or (f.side == "Sell" and p < lim) or rem <= 0:
                break
            gone = min(skip, q)
            skip -= gone
            take = min(rem, q - gone)
            got += take
            cost += take * p
            rem -= take
        self.used[(name, f.side)] = self.used.get((name, f.side), 0.0) + got
        f.filled = floor_step(Decimal(str(got)), self.res.step) if got < float(f.qty) else f.qty
        if f.filled > 0:
            f.avg_price = cost / got
            fee_bps = PERP_TAKER_BPS if category == "linear" else SPOT_TAKER_BPS
            f.fee_usd = round(float(f.filled) * f.avg_price * fee_bps / 1e4, 6)
        f.status = "filled" if f.filled >= f.qty else ("partial" if f.filled > 0 else "unfilled")
        return f

    async def liability(self, it: CarryIntent, borrowed: Decimal, opened_at: datetime) -> Decimal:
        hourly = (self.quote.hourly if self.quote else None) or it.borrow_hourly or Decimal("0")
        hours = math.ceil(max(0.0, (datetime.now(UTC) - opened_at).total_seconds()) / 3600 - 1e-9)
        return borrowed * (1 + hourly * hours)

    async def reconcile(self, it: CarryIntent, perp_qty: Decimal, short_qty: Decimal) -> dict:
        return {"perp_qty": dstr(perp_qty), "spot_short_qty": dstr(short_qty),
                "residual": dstr(perp_qty - short_qty), "source": "simulated"}


class _LiveOps(_Ops):
    """Testnet: send through the transport (host-allowlisted), parse fills."""

    def __init__(self, *a, transport: Transport) -> None:
        super().__init__(*a)
        self.t = transport

    async def _send(self, req: SignedRequest) -> dict[str, Any]:
        await self.ex._bucket(req.venue).take()
        self.res.sent += 1
        try:
            return await self.t.send(req)
        except Exception as e:  # noqa: BLE001 — a transport failure is a failed step, handled by the caller
            return {"retCode": -1, "retMsg": f"{type(e).__name__}: {e}"}

    @staticmethod
    def _ok(venue: str, data: dict) -> bool:
        if venue == "bybit":
            return data.get("retCode") == 0
        return "code" not in data or data.get("code") == 200

    @staticmethod
    def _msg(data: dict) -> str:
        return str(data.get("retMsg") or data.get("msg") or "")

    async def borrow_check(self, it: CarryIntent, qty: Decimal) -> tuple[bool, str]:
        data = await self._send(self.borrow_check_req(it))
        if not self._ok(it.spot_venue, data):
            return False, f"borrow check failed: {self._msg(data)}"
        r = data.get("result") or data
        mx = Decimal(str(r.get("maxTradeQty") if it.spot_venue == "bybit" else r.get("amount") or "0"))
        return (mx >= qty), f"max {dstr(mx)} vs {dstr(qty)}"

    async def margin_check(self, it: CarryIntent, need_usd: Decimal) -> tuple[bool, str]:
        data = await self._send(self._req(self.bybit.wallet()))
        if not self._ok("bybit", data):
            return False, f"wallet query failed: {self._msg(data)}"
        acct = ((data.get("result") or {}).get("list") or [{}])[0]
        avail = Decimal(str(acct.get("totalAvailableBalance") or "0"))
        return avail >= need_usd, f"available {dstr(avail)} vs need {dstr(need_usd)}"

    async def borrow(self, it: CarryIntent, qty: Decimal) -> tuple[bool, str]:
        data = await self._send(self.borrow_req(it, qty))
        return self._ok(it.spot_venue, data), self._msg(data)

    async def repay(self, it: CarryIntent, qty: Decimal) -> tuple[bool, str]:
        data = await self._send(self.repay_req(it, qty))
        return self._ok(it.spot_venue, data), self._msg(data)

    async def order(self, f: LegFill, category: str, price: Decimal, reduce_only: bool = False) -> LegFill:
        data = await self._send(self.order_req(f.venue, category, f.symbol, f.side, f.qty, price, f.link_id,
                                               reduce_only))
        if not self._ok(f.venue, data) and "duplicate" not in self._msg(data).lower():
            f.status, f.detail = "rejected", self._msg(data)
            return f
        # IOC is terminal at once; read the fill by our own id (also covers a
        # duplicate-id answer after a retried submit).
        status_req = (self.bybit.order_status(category=category, link_id=f.link_id) if f.venue == "bybit"
                      else self.binance.order_status(symbol=f.symbol, link_id=f.link_id))
        row: dict = {}
        for _ in range(3):
            st = await self._send(self._req(status_req))
            row = (((st.get("result") or {}).get("list") or [{}])[0] if f.venue == "bybit" else st) or {}
            if row:
                break
            await asyncio.sleep(0.2)
        qty = Decimal(str(row.get("cumExecQty") or row.get("executedQty") or "0"))
        if qty > 0:
            if f.venue == "bybit":
                f.avg_price = float(row.get("avgPrice") or 0) or None
                fee = float(row.get("cumExecFee") or 0)
                # Bybit takes a spot buy's fee in the coin bought, everything else in USDT
                f.fee_usd = fee * (f.avg_price or 0.0) if (category == "spot" and f.side == "Buy") else fee
            else:
                f.avg_price = float(row.get("cummulativeQuoteQty") or 0) / float(qty)
        f.filled = qty
        f.status = "filled" if qty >= f.qty else ("partial" if qty > 0 else "unfilled")
        f.detail = str(row.get("orderStatus") or row.get("status") or "")
        return f

    async def liability(self, it: CarryIntent, borrowed: Decimal, opened_at: datetime) -> Decimal:
        data = await self._send(self._req(self.bybit.wallet(it.coin)))
        coins = (((data.get("result") or {}).get("list") or [{}])[0]).get("coin") or []
        row = next((c for c in coins if c.get("coin") == it.coin), None)
        if row is None:
            return borrowed
        return Decimal(str(row.get("borrowAmount") or "0")) + Decimal(str(row.get("accruedInterest") or "0"))

    async def reconcile(self, it: CarryIntent, perp_qty: Decimal, short_qty: Decimal) -> dict:
        pos = await self._send(self._req(self.bybit.position(it.perp_symbol)))
        rows = (pos.get("result") or {}).get("list") or []
        perp = sum((Decimal(str(r.get("size") or "0")) for r in rows if r.get("side") == "Buy"), Decimal("0"))
        bal = await self._send(self._req(self.bybit.wallet(it.coin)))
        coins = (((bal.get("result") or {}).get("list") or [{}])[0]).get("coin") or []
        crow = next((c for c in coins if c.get("coin") == it.coin), {})
        owed = Decimal(str(crow.get("borrowAmount") or "0"))
        held = Decimal(str(crow.get("walletBalance") or "0"))
        short = max(Decimal("0"), owed - max(held, Decimal("0")))
        return {"perp_qty": dstr(perp), "spot_short_qty": dstr(short), "residual": dstr(perp - short),
                "expected_perp": dstr(perp_qty), "expected_short": dstr(short_qty),
                "matches": abs(perp - perp_qty) <= self.res.step and abs(short - short_qty) <= self.res.step * 2,
                "source": "exchange"}


# ---------------------------------------------------------------- executor

BookFn = Callable[[str, str, str], Awaitable[Book | None]]
SpecFn = Callable[[str, str, str], Awaitable[InstrumentSpec | None]]
QuoteFn = Callable[[str, str], Awaitable[BorrowQuote | None]]


async def _log_alert(level: str, message: str) -> None:
    (logger.critical if level == "critical" else logger.warning)(f"CARRY_ALERT {message}")


async def _default_alert(level: str, message: str) -> None:
    await _log_alert(level, message)
    if level == "critical":
        await _telegram(f"[matrix carry] {message}")


class CarryExecutor:
    """Two-leg carry executor. One instance per process keeps one rate
    limiter. Dry-run is the default and cannot send (NeverSend)."""

    def __init__(
        self,
        *,
        mode: str | None = None,
        transport: Transport | None = None,
        books: BookFn | None = None,
        specs: SpecFn | None = None,
        borrow_quote: QuoteFn | None = None,
        store: CarryStore | None = None,
        alert: AlertFn | None = None,
        clock: Callable[[], float] = time.time,
        max_opens_per_hour: float | None = None,
    ) -> None:
        self.mode = resolve_mode(mode)
        self.transport: Transport = NeverSend() if self.mode == DRY_RUN else (transport or HttpTransport())
        self.books = books or db_book
        self.specs = specs or rest_spec
        self.borrow_quote = borrow_quote or db_borrow_quote
        self.store = store or PredictionContextStore()
        self.alert = alert or (_log_alert if self.mode == DRY_RUN else _default_alert)
        self.clock = clock
        per_h = MAX_OPENS_PER_HOUR if max_opens_per_hour is None else max_opens_per_hour
        self._opens = TokenBucket(rate_per_sec=per_h / 3600.0, capacity=max(1.0, per_h))
        self._buckets: dict[str, TokenBucket] = {}

    def _bucket(self, venue: str) -> TokenBucket:
        if venue not in self._buckets:
            self._buckets[venue] = TokenBucket(rate_per_sec=REQ_PER_SEC, capacity=REQ_PER_SEC)
        return self._buckets[venue]

    # -- setup -------------------------------------------------------------

    def _builders(self) -> tuple[BybitV5Requests, BinanceMarginRequests] | str:
        if self.mode == DRY_RUN:
            return (BybitV5Requests(base=BYBIT_MAINNET, api_key=DRY_RUN_KEY, api_secret=DRY_RUN_SECRET, clock=self.clock),
                    BinanceMarginRequests(base=BINANCE_MAINNET, api_key=DRY_RUN_KEY, api_secret=DRY_RUN_SECRET,
                                          clock=self.clock))
        if os.environ.get("BYBIT_TESTNET", "true").strip().lower() == "false":
            return "testnet mode refused: BYBIT_TESTNET=false (mainnet posture)"
        key = os.environ.get("BYBIT_TESTNET_API_KEY", "").strip()
        secret = os.environ.get("BYBIT_TESTNET_API_SECRET", "").strip()
        if not key or not secret:
            return "testnet credentials missing (BYBIT_TESTNET_API_KEY/SECRET)"
        # Binance margin has no testnet: its builder exists only to fail closed.
        return (BybitV5Requests(base=BYBIT_TESTNET, api_key=key, api_secret=secret, clock=self.clock),
                BinanceMarginRequests(base=BINANCE_MAINNET, api_key="", api_secret="", clock=self.clock))

    async def _gate(self, it: CarryIntent, combined: Decimal, *, closing: bool) -> dict:
        d = await should_submit_live(strategy_id=it.strategy_id, asset_class=it.asset_class,
                                     strategy_version=it.strategy_version, intended_notional_usd=combined,
                                     wallet_id=it.wallet_id, closing=closing)
        return {"allowed": d.allowed, "reasons": d.reasons}

    @staticmethod
    async def _wallet_leg_cap(wallet_id: UUID) -> Decimal | None:
        """Per-leg ceiling from the combined-notional rule: both legs together
        stay within max_position_pct x equity."""
        async with shared_session_scope() as session:
            w = await session.get(Wallet, wallet_id)
        if w is None:
            return None
        return (Decimal(w.cash_usd) + Decimal(w.locked_usd)) * Decimal(w.max_position_pct) / 2

    async def _kill(self, res: CarryResult, reason: str) -> None:
        res.would_kill = True
        await self.alert("critical", f"{res.action} {res.intent.perp_symbol} pred={res.intent.prediction_id}: "
                                     f"{reason}; KILL SWITCH {'(dry-run: not tripped)' if self.mode == DRY_RUN else 'TRIPPED'}")
        res.alerts.append(reason)
        if self.mode == DRY_RUN:
            return
        _KILL.update(halted=True, reason=reason, at=datetime.now(UTC).isoformat())
        async with shared_session_scope() as session:
            await session.execute(
                text("UPDATE wallets SET circuit_tripped_at = now() WHERE id = :w AND circuit_tripped_at IS NULL"),
                {"w": res.intent.wallet_id},
            )

    async def _warn(self, res: CarryResult, msg: str) -> None:
        res.alerts.append(msg)
        await self.alert("warning", f"{res.action} {res.intent.perp_symbol} pred={res.intent.prediction_id}: {msg}")

    async def _prepare(self, it: CarryIntent, res: CarryResult):
        """Refusals that need no market data, then books and specs."""
        if self.mode == TESTNET and not TESTNET_MARGIN.get(it.spot_venue, False):
            return res.finish("refused", f"{it.spot_venue} margin has no testnet; dry-run only")
        if self.mode == TESTNET and _KILL["halted"] and res.action == "open":
            return res.finish("refused", f"kill switch halted: {_KILL['reason']}")
        built = self._builders()
        if isinstance(built, str):
            return res.finish("refused", built)
        perp_b = await self.books("bybit", "linear", it.perp_symbol)
        spot_b = await self.books(it.spot_venue, "spot", it.spot_symbol)
        if perp_b is None or spot_b is None:
            return res.finish("aborted", f"no {'perp' if perp_b is None else 'spot'} book")
        ps = await self.specs("bybit", "linear", it.perp_symbol)
        ss = await self.specs(it.spot_venue, "spot", it.spot_symbol)
        if ps is None or ss is None:
            if self.mode != DRY_RUN:
                return res.finish("aborted", "instrument spec unavailable")
            ps, ss = ps or FALLBACK_SPEC, ss or FALLBACK_SPEC
        res.step = max(ps.qty_step, ss.qty_step)
        return built, {"perp": perp_b, "spot": spot_b}, ps, ss

    @staticmethod
    def _px(mid: float, side: str, band_bps: float, tick: Decimal) -> Decimal:
        raw = Decimal(str(mid)) * (1 + Decimal(str(band_bps if side == "Buy" else -band_bps)) / 10000)
        return floor_step(raw, tick) if side == "Buy" else ceil_step(raw, tick)

    async def _leg(self, ops, it: CarryIntent, res: CarryResult, *, leg: str, attempt: int, qty: Decimal,
                   books: dict, ticks: dict, band: float, reduce_only: bool = False) -> LegFill:
        category = "linear" if leg.startswith("perp") else "spot"
        venue = "bybit" if category == "linear" else it.spot_venue
        side = "Buy" if leg.endswith("buy") else "Sell"
        book = books["perp" if category == "linear" else "spot"]
        f = LegFill(leg=leg, venue=venue, symbol=it.perp_symbol if category == "linear" else it.spot_symbol,
                    side=side, qty=qty, mid=book.mid, link_id=_link(it, res.action, leg, attempt))
        price = self._px(book.mid, side, band, ticks[category])
        f = await ops.order(f, category, price, reduce_only)
        res.legs.append(f)
        return f

    async def _fill_with_retries(self, ops, it, res, *, leg: str, qty: Decimal, books, ticks,
                                 bands: list[float], reduce_only: bool = False) -> Decimal:
        """Fill `qty` on one leg, one IOC per band; returns the qty filled."""
        done = Decimal("0")
        for i, band in enumerate(bands):
            rest = floor_step(qty - done, res.step)
            if rest <= 0:
                break
            f = await self._leg(ops, it, res, leg=leg, attempt=i + 1, qty=rest, books=books, ticks=ticks,
                                band=band, reduce_only=reduce_only)
            done += f.filled
        return done

    def _ops(self, built, res: CarryResult, books: dict, quote: BorrowQuote | None):
        bybit, binance = built
        if self.mode == DRY_RUN:
            return _SimOps(self, res, bybit, binance, books=books, quote=quote)
        return _LiveOps(self, res, bybit, binance, transport=self.transport)

    async def _persist(self, res: CarryResult) -> None:
        try:
            await self.store.save(res.intent.prediction_id, self.mode, res.action, res.record())
        except Exception as e:  # noqa: BLE001 — the log line still carries everything
            logger.warning(f"carry_exec: could not persist {res.action} for {res.intent.prediction_id}: {e}")

    # -- open --------------------------------------------------------------

    async def open(self, it: CarryIntent) -> CarryResult:
        res = CarryResult("open", self.mode, it)
        await self._open(it, res)
        if res.status != "refused":  # a refusal sent nothing; never overwrite a prior record with it
            await self._persist(res)
        (logger.warning if res.status in ("naked", "unwound") else logger.info)(res.log_line())
        return res

    async def _open(self, it: CarryIntent, res: CarryResult) -> CarryResult:
        prior = await self.store.load(it.prediction_id, self.mode, "open")
        if prior:
            if prior.get("status") == "in_flight":
                await self._warn(res, "open record in_flight from an interrupted run: reconcile on the venue by hand")
            return res.finish("refused", f"already executed ({prior.get('status')}); idempotent no-op")
        prep = await self._prepare(it, res)
        if isinstance(prep, CarryResult):
            return prep
        built, books, ps, ss = prep
        if not self._opens.try_take():
            return res.finish("refused", f"carry open rate limit ({MAX_OPENS_PER_HOUR:g}/h)")
        ticks = {"linear": ps.tick, "spot": ss.tick}

        # Size: paper leg, $500 until confirmed, and both legs within the
        # wallet's max_position_pct (the combined-notional rule).
        leg = Decimal(str(it.leg_usd))
        if not it.confirmed:
            leg = min(leg, UNCONFIRMED_MAX_LEG_USD)
        cap = await self._wallet_leg_cap(it.wallet_id)
        if cap is not None:
            leg = min(leg, cap)
        mid = Decimal(str(books["perp"].mid))
        qty = floor_step(leg / mid, res.step)
        res.qty = qty
        res.leg_usd = float(qty * mid)
        res.combined_usd = 2 * res.leg_usd
        if leg < MIN_LEG_USD or qty < max(ps.min_qty, ss.min_qty) or qty * mid < max(ps.min_notional, ss.min_notional):
            return res.finish("aborted", f"leg ${float(leg):.2f} below minimum (qty {dstr(qty)})")

        res.gate = await self._gate(it, Decimal(str(round(res.combined_usd, 2))), closing=False)
        if not res.gate["allowed"] and self.mode != DRY_RUN:
            return res.finish("refused", "live gate refused")

        quote = await self.borrow_quote(it.spot_venue, it.coin)
        if quote and it.borrow_hourly and quote.hourly > it.borrow_hourly * Decimal(str(BORROW_DRIFT_MAX)):
            return res.finish("aborted", f"borrow quote moved {dstr(it.borrow_hourly)} -> {dstr(quote.hourly)}/h "
                                         f"(> x{BORROW_DRIFT_MAX:g})")
        ops = self._ops(built, res, books, quote)

        ok, why = await ops.borrow_check(it, qty)
        if not ok:
            return res.finish("aborted", f"borrow rejected: {why}")
        ok, why = await ops.margin_check(it, Decimal(str(res.combined_usd)) * MARGIN_BUFFER)
        if not ok:
            return res.finish("aborted", f"margin insufficient: {why}")

        if self.mode != DRY_RUN:
            # A crash from here on leaves venue state the next run must not
            # repeat blindly: the marker makes a rerun refuse, not re-borrow.
            await self.store.save(it.prediction_id, self.mode, "open", {"status": "in_flight", "qty": dstr(qty)})
        # 1. borrow
        ok, why = await ops.borrow(it, qty)
        if not ok:
            return res.finish("aborted", f"borrow rejected: {why}")
        min_q = max(ps.min_qty, ss.min_qty, res.step)

        # 2. sell the borrowed coin
        f1 = await self._leg(ops, it, res, leg="spot_sell", attempt=1, qty=qty, books=books, ticks=ticks, band=BAND_BPS)
        q1 = floor_step(f1.filled, res.step)
        if q1 < min_q:
            ok, why = await ops.repay(it, qty - f1.filled)
            if f1.filled > 0:
                await self._warn(res, f"spot dust {dstr(f1.filled)} sold below min qty; left short")
            if not ok:
                await self._warn(res, f"repay after unfilled spot leg failed: {why}")
            return res.finish("aborted", f"spot leg unfilled ({f1.status} {f1.detail})".strip())

        # 3. buy the perp hedge (one retry of the remainder at the same band)
        q2 = floor_step(await self._fill_with_retries(ops, it, res, leg="perp_buy", qty=q1, books=books,
                                                      ticks=ticks, bands=[BAND_BPS, BAND_BPS]), res.step)
        if q2 < min_q:
            # Never leave a naked short: buy the spot back now, bounded slippage.
            got = await self._buyback(ops, it, res, q1, books, ticks, ss)
            if got < q1 - res.step / 2:
                await self._kill(res, f"perp leg failed and spot unwind filled {dstr(got)}/{dstr(q1)}: NAKED SHORT")
                return res.finish("naked", "unwind failed")
            ok, why = await ops.repay(it, qty)
            if not ok:
                await self._warn(res, f"repay after unwind failed: {why}")
            await self._warn(res, "perp leg failed; spot unwound, flat")
            res.qty = Decimal("0")
            return res.finish("unwound", "perp leg unfilled")
        if q2 < q1:
            got = await self._buyback(ops, it, res, q1 - q2, books, ticks, ss)
            if got < (q1 - q2) - res.step / 2:
                await self._kill(res, f"partial perp hedge {dstr(q2)}/{dstr(q1)} and excess spot unwind failed")
                return res.finish("naked", "excess unwind failed")
        # 4. repay what the hedge does not use
        if qty - q2 > 0:
            ok, why = await ops.repay(it, qty - q2)
            if not ok:
                await self._warn(res, f"excess borrow repay failed: {why}")
        res.qty = q2
        res.leg_usd = float(q2 * mid)
        res.combined_usd = 2 * res.leg_usd
        res.reconcile = await ops.reconcile(it, q2, q2)
        if res.reconcile.get("matches") is False:
            await self._warn(res, f"reconcile mismatch {res.reconcile}")
        return res.finish("done" if q2 == qty else "partial", None if q2 == qty else f"hedged {dstr(q2)}/{dstr(qty)}")

    async def _buyback(self, ops, it, res, qty: Decimal, books, ticks, ss: InstrumentSpec) -> Decimal:
        """Buy `qty` coin back on spot (fee taken in the coin, so gross up),
        two widening bounded-slippage attempts. Returns net coin bought."""
        gross = ceil_step(qty / (1 - Decimal(str(SPOT_TAKER_BPS)) / 10000), res.step)
        filled = await self._fill_with_retries(ops, it, res, leg="spot_buy", qty=gross, books=books, ticks=ticks,
                                               bands=[UNWIND_BAND_BPS, 2 * UNWIND_BAND_BPS])
        return filled * (1 - Decimal(str(SPOT_TAKER_BPS)) / 10000)

    # -- close -------------------------------------------------------------

    async def close(self, it: CarryIntent, *, qty: Decimal, opened_at: datetime) -> CarryResult:
        res = CarryResult("close", self.mode, it)
        await self._close(it, res, qty, opened_at)
        if res.status != "refused":
            await self._persist(res)
        (logger.warning if res.status in ("naked", "aborted", "partial") else logger.info)(res.log_line())
        return res

    async def _close(self, it: CarryIntent, res: CarryResult, qty: Decimal, opened_at: datetime) -> CarryResult:
        prior = await self.store.load(it.prediction_id, self.mode, "close")
        if prior and prior.get("status") == "done":
            return res.finish("refused", "already closed; idempotent no-op")
        prep = await self._prepare(it, res)
        if isinstance(prep, CarryResult):
            return prep
        built, books, ps, ss = prep
        ticks = {"linear": ps.tick, "spot": ss.tick}
        mid = Decimal(str(books["perp"].mid))
        res.leg_usd = float(qty * mid)
        res.combined_usd = 2 * res.leg_usd
        # Exits evaluate posture/flag/cert/circuit only: a held position must
        # always be closable.
        res.gate = await self._gate(it, Decimal(str(round(res.combined_usd, 2))), closing=True)
        if not res.gate["allowed"] and self.mode != DRY_RUN:
            return res.finish("refused", "live gate refused the close")
        quote = await self.borrow_quote(it.spot_venue, it.coin)
        ops = self._ops(built, res, books, quote)

        owed = await ops.liability(it, qty, opened_at)
        # 1. buy the coin back (thin leg first: a failure leaves the hedge on)
        got = await self._buyback_close(ops, it, res, owed, books, ticks)
        if got < res.step:
            await self._warn(res, "spot buyback unfilled; still hedged, close deferred")
            return res.finish("aborted", "spot buyback unfilled")
        hedge = min(qty, floor_step(got, res.step))
        # 2. sell the perp; retries widen; failing that we are long naked
        sold = await self._fill_with_retries(ops, it, res, leg="perp_sell", qty=hedge, books=books, ticks=ticks,
                                             bands=[BAND_BPS, UNWIND_BAND_BPS, 2 * UNWIND_BAND_BPS], reduce_only=True)
        if sold < hedge - res.step / 2:
            await self._kill(res, f"spot bought back but perp sell filled {dstr(sold)}/{dstr(hedge)}: NAKED LONG")
            return res.finish("naked", "perp close failed")
        # 3. repay: the liability (interest included, to 8 dp), never more than bought
        q8 = Decimal("0.00000001")
        ok, why = await ops.repay(it, min(owed.quantize(q8, ROUND_CEILING), got.quantize(q8, ROUND_FLOOR)))
        if not ok:
            await self._warn(res, f"repay failed: {why}")
        res.qty = sold
        res.reconcile = await ops.reconcile(it, qty - sold, max(Decimal("0"), owed - got))
        if got < owed - res.step:
            return res.finish("partial", f"bought {dstr(got)}/{dstr(owed)}; remainder still hedged")
        return res.finish("done")

    async def _buyback_close(self, ops, it, res, owed: Decimal, books, ticks) -> Decimal:
        gross = ceil_step(owed / (1 - Decimal(str(SPOT_TAKER_BPS)) / 10000), res.step)
        filled = await self._fill_with_retries(ops, it, res, leg="spot_buy", qty=gross, books=books, ticks=ticks,
                                               bands=[BAND_BPS, UNWIND_BAND_BPS])
        return filled * (1 - Decimal(str(SPOT_TAKER_BPS)) / 10000)


# ---------------------------------------------------------------- shadow mirror

_MIRROR: CarryExecutor | None = None


def mirror_enabled() -> bool:
    return (os.environ.get("MATRIX_CARRY_MIRROR") or "true").strip().lower() != "false"


def _mirror() -> CarryExecutor:
    """Always dry-run, whatever MATRIX_CARRY_EXEC_MODE says: the mirror
    measures the executable path beside paper, it never trades."""
    global _MIRROR
    if _MIRROR is None:
        _MIRROR = CarryExecutor(mode=DRY_RUN)
    return _MIRROR


def _intent(pred, wallet_id: UUID, notional: Decimal, paper: dict | None, confirmed: bool) -> CarryIntent:
    ctx = pred.context or {}
    return CarryIntent(
        prediction_id=pred.id, strategy_id=pred.strategy_id, strategy_version=pred.strategy_version,
        wallet_id=wallet_id, perp_symbol=pred.symbol, spot_venue=str(ctx["spot_venue"]),
        spot_symbol=str(ctx["spot_symbol"]), leg_usd=Decimal(str(notional)), asset_class=pred.asset_class,
        confirmed=confirmed,
        borrow_hourly=Decimal(str(ctx["borrow_rate_hourly"])) if ctx.get("borrow_rate_hourly") else None,
        paper=paper,
    )


async def mirror_paper_open(*, prediction, wallet_id: UUID, notional: Decimal, book_open: dict | None) -> CarryResult | None:
    """Dry-run the carry open beside a paper open of a book-priced carry. Never raises."""
    if not mirror_enabled():
        return None
    try:
        # The paper open already asked the promotion bar: no $500 ceiling on
        # its book_open means `confirmed` (no second edge_study call here).
        confirmed = bool(book_open) and book_open.get("ceiling_usd") is None
        it = _intent(prediction, wallet_id, notional, book_open, confirmed)
        return await asyncio.wait_for(_mirror().open(it), timeout=MIRROR_TIMEOUT_S)
    except Exception as e:  # noqa: BLE001 — the mirror must never break paper
        logger.warning(f"carry_exec mirror open {prediction.symbol} pred={prediction.id}: {type(e).__name__}: {e}")
        return None


async def mirror_paper_close(*, prediction, position, book_close: dict | None) -> CarryResult | None:
    """Dry-run the carry close beside a paper close. Needs the dry-run open
    record (its qty); a carry opened before the mirror existed is skipped."""
    if not mirror_enabled():
        return None
    try:
        ex = _mirror()
        opened = await ex.store.load(prediction.id, DRY_RUN, "open")
        if not opened or opened.get("status") not in ("done", "partial") or Decimal(opened.get("qty") or "0") <= 0:
            logger.info(f"carry_exec[dry_run] close {position.symbol} pred={prediction.id}: no dry-run open "
                        f"record ({(opened or {}).get('status')}); skipped")
            return None
        it = _intent(prediction, position.wallet_id, position.notional_usd, book_close, False)
        opened_at = position.opened_at if position.opened_at.tzinfo else position.opened_at.replace(tzinfo=UTC)
        return await asyncio.wait_for(ex.close(it, qty=Decimal(opened["qty"]), opened_at=opened_at),
                                      timeout=MIRROR_TIMEOUT_S)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"carry_exec mirror close {position.symbol} pred={prediction.id}: {type(e).__name__}: {e}")
        return None

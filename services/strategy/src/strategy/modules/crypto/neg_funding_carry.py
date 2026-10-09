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
carry accounting (settlement-paid funding, four-leg cost) applies unchanged.
"""

from __future__ import annotations

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
                px = (
                    await session.execute(
                        text(
                            "SELECT coalesce(mark_price, last_price) FROM market_ticker_snapshots "
                            "WHERE symbol = :sym AND exchange = 'bybit' AND snapshot_ts > :since "
                            "ORDER BY snapshot_ts DESC LIMIT 1"
                        ),
                        {"sym": symbol, "since": now - timedelta(minutes=5)},
                    )
                ).scalar()
                if px is None:
                    continue
                mag = -rate
                conf = max(min(mag / FUNDING_CAP, Decimal("1")), Decimal("0.10"))
                hourly, venue = borrow
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
                            f"spot (borrow {hourly * 80000:.2f} bps/8h on {venue}) / long perp for 48h"
                        ),
                        context={
                            "funding_rate_8h": str(rate),
                            "settled_at": settled_at.isoformat(),
                            "borrow_rate_hourly": str(hourly),
                            "borrow_venue": venue,
                        },
                    )
                )
        return drafts

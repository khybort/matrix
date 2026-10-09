"""Funding as the venue actually pays it: at settlements, at the rate in force.

Two measured defects in the carry book (2026-10-09) come from treating the
ticker's *current* funding rate as if it were a continuous accrual:

1. **Placeholder at settlement.** For a minute after each settlement Bybit's
   ticker reports `+0.0000125` (the base interest component) before the next
   predicted rate arrives. An inverse carry reads that as a sign flip, so
   every one of them was closed at the first settlement it reached — 51 of 51
   `funding_flip` exits, mean hold 1-4 h of a 48 h horizon.
2. **Accrual at the closing rate.** PnL was `notional x hours/8 x rate_at_close`.
   At a flip exit the closing rate is the placeholder (~0), so the hold earned
   nothing and the trade booked exactly its four-leg fee: -24.0 bps, 0 % win.
   Real funding is a discrete payment to whoever holds at the settlement
   timestamp, at the rate published just before it.

Replayed on the same signals with settlement accounting and held to horizon,
inverse_carry earned +60.9 bps funding gross (+31.3 net, n=61; +14.9 net,
t=0.95 on 25 independent episodes) where the books said -27 bps. So the 0 %
win rate was the accounting, not the market — while xexch_funding_arb and
cash_and_carry stay negative under either model.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import text

from matrix_shared.db import local_session_scope

# Only a snapshot this close to a settlement is trusted as "the rate in force"
# for it; older ones belong to a feed that was down.
SETTLEMENT_WINDOW = timedelta(minutes=10)
# A flip must persist this long before it closes a carry: the post-settlement
# placeholder lasts under two minutes.
FLIP_CONFIRM = timedelta(minutes=5)


def settled_sum(
    snapshots: Iterable[tuple[datetime, Decimal, datetime | None]],
    start: datetime,
    end: datetime,
) -> Decimal:
    """Sum of the rates paid at every settlement S with start < S <= end.

    `snapshots` are (snapshot_ts, funding_rate, next_funding_ts). The rate paid
    at S is the last snapshot taken before S that named S as its next
    settlement, within SETTLEMENT_WINDOW of it. Signed as the venue reports it
    (positive = longs pay shorts).
    """
    last: dict[datetime, tuple[datetime, Decimal]] = {}
    for ts, rate, nxt in snapshots:
        if nxt is None or rate is None or not (start < nxt <= end):
            continue
        if not (nxt - SETTLEMENT_WINDOW < ts < nxt):
            continue
        prev = last.get(nxt)
        if prev is None or ts > prev[0]:
            last[nxt] = (ts, Decimal(rate))
    return sum((r for _, r in last.values()), Decimal("0"))


def flip_confirmed(captured: Iterable[Decimal]) -> bool:
    """True only when every captured-rate reading in the confirmation window is
    adverse. One favourable reading — or none at all — keeps the carry open."""
    seen = False
    for r in captured:
        seen = True
        if r >= 0:
            return False
    return seen


async def _snapshots(
    symbol: str, exchange: str, since: datetime, until: datetime
) -> list[tuple[datetime, Decimal, datetime | None]]:
    async with local_session_scope() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT snapshot_ts, funding_rate, next_funding_ts "
                    "FROM market_ticker_snapshots "
                    "WHERE symbol = :sym AND exchange = :ex "
                    "AND snapshot_ts >= :since AND snapshot_ts <= :until "
                    "AND funding_rate IS NOT NULL"
                ),
                {"sym": symbol, "ex": exchange, "since": since, "until": until},
            )
        ).all()
    return [(ts, Decimal(r), nxt) for ts, r, nxt in rows]


async def settled_funding(symbol: str, exchange: str, start: datetime, end: datetime) -> Decimal:
    """Rates paid on `exchange` for `symbol` at settlements inside (start, end]."""
    snaps = await _snapshots(symbol, exchange, start - SETTLEMENT_WINDOW, end)
    return settled_sum(snaps, start, end)


async def recent_rates(symbol: str, exchange: str, now: datetime) -> list[Decimal]:
    snaps = await _snapshots(symbol, exchange, now - FLIP_CONFIRM, now)
    return [r for _, r, _ in sorted(snaps, key=lambda s: s[0])]

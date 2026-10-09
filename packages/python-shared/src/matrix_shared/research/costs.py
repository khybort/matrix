"""Walked-book cost: taker fees plus the distance from mid of every fill.

`walk_bps` is the same function as `backtest.carry_books.walk_bps` (and the
round-3 / 3b / shb scripts' `walk`). It lives here because research must run
in images that do not ship the backtest service; carry_books keeps its copy
until its owner switches the import to this module (one-line change, no
behaviour change — the two are tested against the same cases).

Convention (all rounds since the 2026-10-09 adversarial check): today's full
public depth, one fetch per (venue, symbol), applied to every past episode of
that symbol, at a stated USD size per leg. A missing or too-thin book means
the episode is excluded and counted, never priced at a default.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

Levels = Sequence[tuple[float, float]]  # (price, qty), best first

# VIP0 taker fees, bps. Gate charges max(5.0, the contract's own field).
TAKER_BPS = {
    ("bybit", "perp"): 5.5,
    ("bybit", "spot"): 10.0,
    ("binance", "perp"): 5.0,
    ("binance", "spot"): 10.0,
    ("okx", "perp"): 5.0,
    ("bitget", "perp"): 6.0,
    ("gate", "perp"): 5.0,
}


def parse_levels(raw) -> list[tuple[float, float]]:
    return [(float(p), float(q)) for p, q, *_ in raw or [] if float(q) > 0]


def mid(bids: Levels, asks: Levels) -> float:
    return (bids[0][0] + asks[0][0]) / 2


def walk_bps(levels: Levels, mid_px: float, usd: float) -> float | None:
    """Average distance from mid (bps) of a taker fill of `usd` walking
    `levels`; None when the book is too thin to fill it."""
    if usd <= 0 or mid_px <= 0:
        return 0.0
    rem, cost = usd, 0.0
    for p, q in levels:
        take = min(rem, p * q)
        cost += take * abs(p - mid_px) / mid_px
        rem -= take
        if rem <= 1e-9:
            return cost / usd * 1e4
    return None


def round_trip_bps(bids: Levels, asks: Levels, usd: float) -> float | None:
    """Walk to open and to close one leg (sell into bids + buy from asks)."""
    if not bids or not asks:
        return None
    m = mid(bids, asks)
    a, b = walk_bps(bids, m, usd), walk_bps(asks, m, usd)
    return None if a is None or b is None else a + b


def hedged_cost_bps(
    leg_a: tuple[Levels, Levels],
    leg_b: tuple[Levels, Levels],
    usd: float,
    fees_bps: float,
) -> float:
    """Four taker fills (both legs opened and closed) plus `fees_bps` (the four
    taker fees). NaN when either book is missing or too thin — the caller
    excludes and counts the episode."""
    a = round_trip_bps(*leg_a, usd)
    b = round_trip_bps(*leg_b, usd)
    if a is None or b is None:
        return math.nan
    return fees_bps + a + b


def fees(*legs: tuple[str, str]) -> float:
    """Sum of round-trip taker fees for the given (venue, kind) legs."""
    return sum(2 * TAKER_BPS[leg] for leg in legs)

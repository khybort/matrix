"""Funding per settlement, as every 2026-10 round booked it.

Rules (each one was checked by the adversarial check of 2026-10-09):
- every raw settled rate inside (entry, exit] counts once — summing raw rates
  handles 1/2/4/8 h intervals and interval switches mid-episode, no
  normalisation;
- a long perp pays a positive rate, a short receives it;
- notional is marked to market: each settlement is scaled by the perp price at
  that settlement (close of the 1h bar ending at ceil_hour(t)) over the entry
  price; a missing price counts at entry notional (factor 1);
- a settlement exactly at entry is not ours (the position opens after it); one
  exactly at exit is.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta


def ceil_hour(t: datetime) -> datetime:
    floor = t.replace(minute=0, second=0, microsecond=0)
    return floor if floor == t else floor + timedelta(hours=1)


def funding_bps(
    settlements: Iterable[tuple[datetime, float]],
    entry: datetime,
    exit_: datetime,
    perp_side: int,
    entry_price: float,
    price_at: Callable[[datetime], float | None] | None = None,
) -> tuple[float, int]:
    """(funding received in bps of entry notional, settlements counted).

    `perp_side` is +1 long / -1 short. `price_at(t)` returns the perp close of
    the 1h bar ending at t (None when absent)."""
    if perp_side not in (1, -1):
        raise ValueError("perp_side must be +1 or -1")
    total, n = 0.0, 0
    for t, rate in settlements:
        if not (entry < t <= exit_):
            continue
        f = 1.0
        if price_at is not None:
            px = price_at(ceil_hour(t))
            if px is not None and math.isfinite(px) and entry_price > 0:
                f = px / entry_price
        total += rate * f
        n += 1
    return -perp_side * total * 1e4, n

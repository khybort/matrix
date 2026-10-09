"""Episodes: one sample per bet, entered strictly after the information it uses.

An `Episode` is what a cell's builder returns for every trade it would have
made. The harness, not the builder, enforces the two rules every 2026-10 round
re-implemented by hand:

- **information time**: `entry > info_time` by at least the study's
  `min_entry_lag_s` (rounds 1-3b: one full bar). A builder that enters on the
  bar its signal was computed from is rejected, not corrected.
- **one sample per episode**: per (cell, symbol) no new entry before the
  previous exit (`non_overlapping`). Builders may emit every signal; overlaps
  are dropped here, in signal order, exactly as the rounds' `busy` loop did.

Signals from the live book (predictions rows) collapse with
`edge_study.one_per_episode`, which owns that definition for the engine;
`collapse_signals` is a thin re-export so research and the engine agree.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta


class LookaheadError(ValueError):
    """An episode enters at or before the time its signal became known."""


class WindowError(ValueError):
    """An episode's signal lies outside the split it was built for."""


@dataclass(frozen=True)
class Episode:
    symbol: str
    info_time: datetime  # when the signal's last input became public
    entry_time: datetime
    exit_time: datetime
    net_bps: float
    parts: dict = field(default_factory=dict)  # gross / funding / hedge / cost ... (descriptive)


def check_information_time(episodes: Iterable[Episode], min_lag: timedelta) -> None:
    for e in episodes:
        if not e.entry_time > e.info_time or e.entry_time - e.info_time < min_lag:
            raise LookaheadError(
                f"{e.symbol}: entry {e.entry_time.isoformat()} is not at least {min_lag} after "
                f"its information time {e.info_time.isoformat()}"
            )
        if e.exit_time < e.entry_time:
            raise LookaheadError(f"{e.symbol}: exit {e.exit_time.isoformat()} before entry")


def check_window(episodes: Iterable[Episode], start: datetime, end: datetime) -> None:
    for e in episodes:
        if not (start <= e.info_time < end):
            raise WindowError(
                f"{e.symbol}: signal at {e.info_time.isoformat()} outside "
                f"[{start.isoformat()}, {end.isoformat()})"
            )


def non_overlapping(episodes: Iterable[Episode]) -> list[Episode]:
    """Per symbol, keep an episode only if its signal comes at or after the
    previous kept episode's exit. Order: signal time, then entry time.

    An episode whose net is not finite (no book to price it, say) still
    occupies its symbol until its exit — the slot was taken by a bet we could
    not price, as in round 3 — and is dropped afterwards by `priced`."""
    busy: dict[str, datetime] = {}
    out: list[Episode] = []
    for e in sorted(episodes, key=lambda x: (x.info_time, x.entry_time, x.symbol)):
        until = busy.get(e.symbol)
        if until is not None and e.info_time < until:
            continue
        busy[e.symbol] = e.exit_time
        out.append(e)
    return out


def priced(episodes: list[Episode]) -> tuple[list[Episode], int]:
    """(episodes with a finite net, number dropped for lack of one)."""
    kept = [e for e in episodes if math.isfinite(e.net_bps)]
    return kept, len(episodes) - len(kept)


def collapse_signals(rows: list[dict]) -> list[dict]:
    """Engine signals (predictions rows) -> one per episode, via edge_study."""
    from matrix_shared.edge_study import one_per_episode  # heavy import, only when used

    return one_per_episode(rows)

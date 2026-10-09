"""NYSE/Nasdaq trading calendar — deterministic, dependency-free.

Computes US equity-market holidays (including Good Friday and the floating
Monday holidays) and the 13:00 ET early-close half-days for *any* year, so we
never ship a stale hardcoded table. Shared by the `UsMarket` adapter, the US
bar poller, and the US strategy helpers — one source of truth for "is the US
session open right now?".

Regular session: 09:30–16:00 America/New_York, Mon–Fri.
Early-close days (day after Thanksgiving, July 3 when it's a trading day,
Dec 24 when it's a trading day): close at 13:00 ET.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from functools import lru_cache
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")

REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)

# weekday() codes
_MON, _THU, _FRI, _SAT, _SUN = 0, 3, 4, 5, 6


def _easter(year: int) -> date:
    """Gregorian Easter Sunday (Anonymous/Meeus algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ll = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ll) // 451
    month = (h + ll - 7 * m + 114) // 31
    day = ((h + ll - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The nth (1-based) `weekday` in `month` (e.g. 3rd Monday)."""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return date(year, month, 1 + offset + (n - 1) * 7)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """The last `weekday` in `month` (e.g. last Monday of May)."""
    # First day of next month, minus one day = last day of this month.
    if month == 12:
        last = date(year, 12, 31)
    else:
        last = date(year, month + 1, 1).fromordinal(date(year, month + 1, 1).toordinal() - 1)
    offset = (last.weekday() - weekday) % 7
    return last.fromordinal(last.toordinal() - offset)


def _observed(d: date) -> date:
    """NYSE weekend-observation rule: Sat→Fri before, Sun→Mon after."""
    if d.weekday() == _SAT:
        return d.fromordinal(d.toordinal() - 1)
    if d.weekday() == _SUN:
        return d.fromordinal(d.toordinal() + 1)
    return d


@lru_cache(maxsize=64)
def holidays(year: int) -> frozenset[date]:
    """Full-closure NYSE holidays for `year`."""
    good_friday = _easter(year).fromordinal(_easter(year).toordinal() - 2)
    days = {
        _observed(date(year, 1, 1)),           # New Year's Day
        _nth_weekday(year, 1, _MON, 3),         # MLK Jr. Day
        _nth_weekday(year, 2, _MON, 3),         # Washington's Birthday
        good_friday,                            # Good Friday
        _last_weekday(year, 5, _MON),           # Memorial Day
        _nth_weekday(year, 9, _MON, 1),         # Labor Day
        _nth_weekday(year, 11, _THU, 4),        # Thanksgiving
        _observed(date(year, 12, 25)),          # Christmas
        _observed(date(year, 7, 4)),            # Independence Day
    }
    if year >= 2022:
        days.add(_observed(date(year, 6, 19)))  # Juneteenth (federal since 2021)
    return frozenset(days)


@lru_cache(maxsize=64)
def early_closes(year: int) -> frozenset[date]:
    """Half-days: market closes 13:00 ET instead of 16:00."""
    out: set[date] = set()
    hol = holidays(year)

    # Day after Thanksgiving (always a Friday).
    thanksgiving = _nth_weekday(year, 11, _THU, 4)
    out.add(thanksgiving.fromordinal(thanksgiving.toordinal() + 1))

    # July 3 when it's a weekday and a trading day (eve of Independence Day).
    jul3 = date(year, 7, 3)
    if jul3.weekday() < _SAT and jul3 not in hol:
        out.add(jul3)

    # Dec 24 when it's a weekday and a trading day (Christmas Eve).
    dec24 = date(year, 12, 24)
    if dec24.weekday() < _SAT and dec24 not in hol:
        out.add(dec24)

    return frozenset(out)


def is_trading_day(d: date) -> bool:
    """True if `d` is a regular Mon–Fri NYSE trading day (not a holiday)."""
    if d.weekday() >= _SAT:
        return False
    return d not in holidays(d.year)


def session_close(d: date) -> time:
    """Close time for `d` — 13:00 ET on early-close days, else 16:00 ET."""
    return EARLY_CLOSE if d in early_closes(d.year) else REGULAR_CLOSE


def is_session_open(ts: datetime | None = None) -> bool:
    """True if `ts` falls inside the US regular cash session.

    Naive datetimes are treated as UTC (matches the rest of the codebase).
    """
    now = ts or datetime.now(NY)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    now = now.astimezone(NY)
    d = now.date()
    if not is_trading_day(d):
        return False
    return REGULAR_OPEN <= now.time() < session_close(d)

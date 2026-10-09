"""Unit tests for the dependency-free NYSE calendar."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from matrix_shared.markets import us_calendar

NY = ZoneInfo("America/New_York")


def test_fixed_holidays_2026() -> None:
    hols = us_calendar.holidays(2026)
    assert date(2026, 1, 1) in hols       # New Year (Thu)
    assert date(2026, 1, 19) in hols      # MLK (3rd Mon Jan)
    assert date(2026, 2, 16) in hols      # Washington (3rd Mon Feb)
    assert date(2026, 5, 25) in hols      # Memorial (last Mon May)
    assert date(2026, 9, 7) in hols       # Labor (1st Mon Sep)
    assert date(2026, 11, 26) in hols     # Thanksgiving (4th Thu Nov)
    assert date(2026, 12, 25) in hols     # Christmas (Fri)


def test_good_friday_2026() -> None:
    # Easter 2026 = April 5 → Good Friday = April 3.
    assert date(2026, 4, 3) in us_calendar.holidays(2026)


def test_independence_day_observed_2026() -> None:
    # July 4 2026 is a Saturday → observed Friday July 3.
    hols = us_calendar.holidays(2026)
    assert date(2026, 7, 3) in hols
    assert date(2026, 7, 4) not in hols


def test_juneteenth_only_from_2022() -> None:
    assert date(2026, 6, 19) in us_calendar.holidays(2026)
    assert date(2021, 6, 18) not in us_calendar.holidays(2021)


def test_is_trading_day() -> None:
    assert us_calendar.is_trading_day(date(2026, 1, 5)) is True   # Mon
    assert us_calendar.is_trading_day(date(2026, 1, 3)) is False  # Sat
    assert us_calendar.is_trading_day(date(2026, 1, 19)) is False  # MLK


def test_early_close_day_after_thanksgiving() -> None:
    day_after = date(2026, 11, 27)  # Friday after Thanksgiving
    assert day_after in us_calendar.early_closes(2026)
    assert us_calendar.session_close(day_after) == us_calendar.EARLY_CLOSE
    # 12:00 ET open, 14:00 ET closed (after the 13:00 early close).
    assert us_calendar.is_session_open(datetime(2026, 11, 27, 12, 0, tzinfo=NY)) is True
    assert us_calendar.is_session_open(datetime(2026, 11, 27, 14, 0, tzinfo=NY)) is False


def test_regular_session_bounds() -> None:
    mon = datetime(2026, 1, 5, 9, 30, tzinfo=NY)
    assert us_calendar.is_session_open(mon) is True
    assert us_calendar.is_session_open(datetime(2026, 1, 5, 16, 0, tzinfo=NY)) is False
    assert us_calendar.is_session_open(datetime(2026, 1, 5, 15, 59, tzinfo=NY)) is True


def test_naive_datetime_treated_as_utc() -> None:
    # 15:00 UTC Monday = 10:00 ET — inside the session.
    naive_utc = datetime(2026, 1, 5, 15, 0, 0)
    assert us_calendar.is_session_open(naive_utc) is True

"""Settlement-based funding and confirmed flips (backtest.carry_funding)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from backtest.carry_funding import flip_confirmed, settled_sum

T0 = datetime(2026, 9, 25, 13, 0, tzinfo=UTC)
PLACEHOLDER = Decimal("0.0000125")


def _hourly_feed(hours: int, rate: str) -> list[tuple[datetime, Decimal, datetime]]:
    """Bybit-shaped feed: the predicted rate during the hour, and the
    +0.0000125 placeholder for the first minute after each settlement."""
    out = []
    for h in range(hours):
        start = T0 + timedelta(hours=h)
        nxt = start + timedelta(hours=1)
        out.append((start + timedelta(seconds=20), PLACEHOLDER, nxt))
        for m in range(2, 60, 5):
            out.append((start + timedelta(minutes=m), Decimal(rate), nxt))
    return out


def test_pays_rate_in_force_at_each_crossed_settlement():
    feed = _hourly_feed(4, "-0.0008")
    # opened 13:01, closed 15:30 -> crosses 14:00 and 15:00
    got = settled_sum(feed, T0 + timedelta(minutes=1), T0 + timedelta(hours=2, minutes=30))
    assert got == Decimal("-0.0016")


def test_placeholder_is_never_the_paid_rate():
    feed = _hourly_feed(2, "-0.0008")
    got = settled_sum(feed, T0, T0 + timedelta(hours=2))
    assert got == Decimal("-0.0016")


def test_hold_inside_one_interval_pays_nothing():
    feed = _hourly_feed(2, "0.001")
    assert settled_sum(feed, T0 + timedelta(minutes=1), T0 + timedelta(minutes=58)) == 0


def test_stale_snapshot_far_from_settlement_is_ignored():
    nxt = T0 + timedelta(hours=1)
    feed = [(T0 + timedelta(minutes=5), Decimal("0.01"), nxt)]
    assert settled_sum(feed, T0, nxt) == 0


def test_flip_needs_every_reading_adverse():
    assert flip_confirmed([Decimal("-0.0001"), Decimal("-0.0002")])
    assert not flip_confirmed([Decimal("-0.0001"), PLACEHOLDER])
    assert not flip_confirmed([])

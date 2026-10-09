"""Strategies stand down on a symbol whose newest datum is older than its
market's threshold (2026-09-30 outage: signals flowed on prices days old)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from strategy.base import PredictionDraft
from strategy.freshness import drop_stale

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)


def _draft(symbol: str, asset_class: str = "crypto") -> PredictionDraft:
    return PredictionDraft(
        id=uuid.uuid4(), strategy_id="s", strategy_version=1, generated_at=NOW, symbol=symbol,
        exchange="bybit", asset_class=asset_class, side="long", confidence=Decimal("0.5"),
        horizon_seconds=600, entry_price_ref=Decimal("100"),
    )


def test_fresh_symbol_kept_stale_and_missing_dropped():
    latest = {
        ("crypto", "BTCUSDT"): NOW - timedelta(seconds=20),
        ("crypto", "ETHUSDT"): NOW - timedelta(days=3),
    }
    kept = drop_stale([_draft("BTCUSDT"), _draft("ETHUSDT"), _draft("SOLUSDT")], latest, NOW)
    assert [d.symbol for d in kept] == ["BTCUSDT"]


def test_threshold_is_per_market():
    # A 20-minute-old BIST bar is the feed's normal delay; for crypto it is an outage.
    latest = {("bist", "THYAO"): NOW - timedelta(minutes=20), ("crypto", "THYAO"): NOW - timedelta(minutes=20)}
    kept = drop_stale([_draft("THYAO", "bist"), _draft("THYAO", "crypto")], latest, NOW)
    assert [d.asset_class for d in kept] == ["bist"]


def test_market_without_threshold_passes():
    assert len(drop_stale([_draft("X", "other")], {}, NOW)) == 1

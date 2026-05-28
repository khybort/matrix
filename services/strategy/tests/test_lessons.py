"""Unit tests for strategy.lessons.filter_drafts.

Uses an in-memory monkey-patch of _load_avoid_filters so we don't touch
the DB for the matcher logic itself. The DB load path is covered by the
agent_lessons feeder tests.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from strategy import lessons
from strategy.base import PredictionDraft


def _draft(strategy_id: str, symbol: str, side: str = "long", asset_class: str = "crypto") -> PredictionDraft:
    return PredictionDraft(
        strategy_id=strategy_id,
        strategy_version=1,
        symbol=symbol,
        exchange="bybit",
        side=side,
        confidence=Decimal("0.5"),
        horizon_seconds=600,
        entry_price_ref=Decimal("100"),
        generated_at=datetime.now(timezone.utc),
        asset_class=asset_class,
    )


@pytest.fixture(autouse=True)
def _clear_cache():
    lessons._cache = None
    lessons._cache_ts = 0.0
    yield
    lessons._cache = None
    lessons._cache_ts = 0.0


@pytest.mark.asyncio
async def test_no_lessons_returns_all(monkeypatch):
    async def _empty():
        return []
    monkeypatch.setattr(lessons, "_load_avoid_filters", _empty)

    drafts = [_draft("s1", "BTCUSDT"), _draft("s2", "ETHUSDT")]
    out = await lessons.filter_drafts(drafts)
    assert len(out) == 2


@pytest.mark.asyncio
async def test_symbol_only_filter_drops_match(monkeypatch):
    async def _ls():
        return [{"strategy_id": "s1", "asset_class": "crypto", "filter": {"symbol": "BNBUSDT"}}]
    monkeypatch.setattr(lessons, "_load_avoid_filters", _ls)

    drafts = [
        _draft("s1", "BNBUSDT"),    # dropped
        _draft("s1", "BTCUSDT"),    # kept (different symbol)
        _draft("s2", "BNBUSDT"),    # kept (different strategy)
    ]
    out = await lessons.filter_drafts(drafts)
    syms = {(d.strategy_id, d.symbol) for d in out}
    assert syms == {("s1", "BTCUSDT"), ("s2", "BNBUSDT")}


@pytest.mark.asyncio
async def test_side_plus_symbol_filter(monkeypatch):
    async def _ls():
        return [{
            "strategy_id": "matrix_agent",
            "asset_class": "crypto",
            "filter": {"symbol": "BTCUSDT", "side": "long"},
        }]
    monkeypatch.setattr(lessons, "_load_avoid_filters", _ls)

    drafts = [
        _draft("matrix_agent", "BTCUSDT", side="long"),   # dropped
        _draft("matrix_agent", "BTCUSDT", side="short"),  # kept (side differs)
        _draft("matrix_agent", "ETHUSDT", side="long"),   # kept (symbol differs)
    ]
    out = await lessons.filter_drafts(drafts)
    assert len(out) == 2
    for d in out:
        assert not (d.symbol == "BTCUSDT" and d.side == "long")


@pytest.mark.asyncio
async def test_asset_class_isolates(monkeypatch):
    """A crypto lesson must not affect bist drafts (and vice versa)."""
    async def _ls():
        return [{"strategy_id": "s1", "asset_class": "crypto", "filter": {"symbol": "SHARED"}}]
    monkeypatch.setattr(lessons, "_load_avoid_filters", _ls)

    drafts = [
        _draft("s1", "SHARED", asset_class="crypto"),   # dropped
        _draft("s1", "SHARED", asset_class="bist"),     # kept
    ]
    out = await lessons.filter_drafts(drafts)
    assert len(out) == 1
    assert out[0].asset_class == "bist"


@pytest.mark.asyncio
async def test_empty_filter_does_not_block(monkeypatch):
    """A lesson with an empty pattern_filter must not silently drop everything."""
    async def _ls():
        return [{"strategy_id": "s1", "asset_class": "crypto", "filter": {}}]
    monkeypatch.setattr(lessons, "_load_avoid_filters", _ls)

    drafts = [_draft("s1", "BTCUSDT"), _draft("s1", "ETHUSDT")]
    out = await lessons.filter_drafts(drafts)
    assert len(out) == 2

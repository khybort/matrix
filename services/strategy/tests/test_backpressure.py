"""apply_backpressure keeps the highest-confidence drafts that fit each
(strategy, market, shadow) group's room."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from strategy import persist as P
from strategy.base import PredictionDraft

pytestmark = pytest.mark.asyncio


def _draft(sid: str, conf: str, shadow: bool = False) -> PredictionDraft:
    return PredictionDraft(
        id=uuid.uuid4(), strategy_id=sid, strategy_version=1, generated_at=datetime.now(timezone.utc),
        symbol="BTCUSDT", exchange="bybit", asset_class="crypto", side="long", confidence=Decimal(conf),
        horizon_seconds=600, entry_price_ref=Decimal("100"), thesis="t",
        context={"is_shadow": True} if shadow else {},
    )


async def test_trims_each_group_to_its_room_keeping_best_confidence(monkeypatch):
    rooms = {("flood", "crypto", False): 2, ("flood", "crypto", True): 0, ("calm", "crypto", False): 10}

    async def fake_room(sid, ac, *, shadow=False):
        return rooms[(sid, ac, shadow)]
    monkeypatch.setattr("matrix_shared.backpressure.room", fake_room)
    drafts = [_draft("flood", "0.3"), _draft("flood", "0.9"), _draft("flood", "0.6"),
              _draft("flood", "0.8", shadow=True), _draft("calm", "0.2")]
    kept = await P.apply_backpressure(drafts)
    flood = sorted(d.confidence for d in kept if d.strategy_id == "flood" and not d.context.get("is_shadow"))
    assert flood == [Decimal("0.6"), Decimal("0.9")]
    assert not any(d.context.get("is_shadow") for d in kept)
    assert sum(1 for d in kept if d.strategy_id == "calm") == 1

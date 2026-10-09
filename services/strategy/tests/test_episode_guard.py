"""persist drops re-emissions of a bet that is still inside an earlier
prediction's horizon (one prediction per bet, not one per tick)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from strategy import persist as P
from strategy.base import PredictionDraft

pytestmark = pytest.mark.asyncio


def _draft(sid: str = "s", symbol: str = "BTCUSDT", side: str = "long", shadow: bool = False) -> PredictionDraft:
    return PredictionDraft(
        id=uuid.uuid4(), strategy_id=sid, strategy_version=1, generated_at=datetime.now(timezone.utc),
        symbol=symbol, exchange="bybit", asset_class="crypto", side=side, confidence=Decimal("0.5"),
        horizon_seconds=600, entry_price_ref=Decimal("100"), thesis="t",
        context={"is_shadow": True} if shadow else {},
    )


def test_live_bet_is_not_emitted_again():
    live = {("s", "crypto", "BTCUSDT", "long", False)}
    kept = P.drop_reemissions([_draft(), _draft(symbol="ETHUSDT")], live)
    assert [d.symbol for d in kept] == ["ETHUSDT"]


def test_same_bet_twice_in_one_batch_is_kept_once():
    kept = P.drop_reemissions([_draft(), _draft()], set())
    assert len(kept) == 1


def test_other_side_strategy_and_shadow_are_different_bets():
    live = {("s", "crypto", "BTCUSDT", "long", False)}
    kept = P.drop_reemissions(
        [_draft(side="short"), _draft(sid="other"), _draft(shadow=True)], live
    )
    assert len(kept) == 3


async def test_guard_consults_live_bets(monkeypatch):
    async def fake_live(drafts):
        return {("s", "crypto", "BTCUSDT", "long", False)}

    monkeypatch.setattr(P, "_live_bets", fake_live)
    kept = await P.apply_episode_guard([_draft(), _draft(symbol="SOLUSDT")])
    assert [d.symbol for d in kept] == ["SOLUSDT"]

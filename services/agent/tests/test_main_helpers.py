"""Pure helpers in agent.main (no DB)."""

from __future__ import annotations

from agent.main import _asset_canonical


def test_crypto_strips_usdt_suffix():
    assert _asset_canonical("BTCUSDT", "crypto") == "BTC"
    assert _asset_canonical("ETHUSDC", "crypto") == "ETH"


def test_crypto_without_known_quote_is_unchanged():
    assert _asset_canonical("WEIRD", "crypto") == "WEIRD"


def test_bist_symbol_is_unchanged():
    assert _asset_canonical("THYAO", "bist") == "THYAO"


import pytest


@pytest.mark.asyncio
async def test_trim_to_room_keeps_best_edge_symbols_per_market(monkeypatch):
    from agent import main as M

    async def fake_room(sid, ac, *, shadow=False):
        assert sid == M.AGENT_STRATEGY_ID
        return {"crypto": 2, "bist": 5}[ac]
    monkeypatch.setattr("matrix_shared.backpressure.room", fake_room)
    targets = [("A", "crypto"), ("B", "crypto"), ("C", "crypto"), ("X", "bist")]
    out = await M._trim_to_room(targets, {"A": 0.2, "B": 0.9, "C": 0.7})
    assert set(out) == {("B", "crypto"), ("C", "crypto"), ("X", "bist")}

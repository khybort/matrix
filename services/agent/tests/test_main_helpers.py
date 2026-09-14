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


def test_hold_cooldown_hides_symbol_until_expiry(monkeypatch):
    from agent import main as M
    monkeypatch.setattr(M, "HOLD_COOLDOWN_S", 300.0)
    M._hold_until.clear()
    targets = [("A", "crypto"), ("B", "crypto")]
    M._mark_hold("A", "crypto", 1000.0)
    assert M._drop_held(targets, 1000.0 + 10) == [("B", "crypto")]
    assert M._drop_held(targets, 1000.0 + 301) == targets
    monkeypatch.setattr(M, "HOLD_COOLDOWN_S", 0.0)
    M._mark_hold("B", "crypto", 2000.0)
    assert M._drop_held(targets, 2000.0) == targets  # cooldown disabled
    M._hold_until.clear()


@pytest.mark.asyncio
async def test_resolve_targets_skips_a_market_whose_universe_fails(monkeypatch):
    from agent import main as M

    class _Mkt:
        def __init__(self, name, ok):
            self.name, self.asset_class, self._ok = name, name, ok
        def is_session_open(self):
            return True
        async def universe(self, db):
            if not self._ok:
                raise RuntimeError('relation "us_symbols" does not exist')
            return ["AAA", "BBB"]

    monkeypatch.setattr(M, "all_markets", lambda: [_Mkt("crypto", True), _Mkt("us", False)])
    class _Scope:
        async def __aenter__(self): return None
        async def __aexit__(self, *a): return False
    monkeypatch.setattr(M, "session_scope", lambda: _Scope())
    assert await M._resolve_targets([]) == [("AAA", "crypto"), ("BBB", "crypto")]


def test_universe_cap_keeps_best_edges_per_market():
    from agent import main as M
    targets = [(f"S{i}", "us") for i in range(8)] + [("BTC", "crypto")]
    edges = {"S7": 0.9, "S3": 0.8}
    out = M._cap_universe(targets, edges, cap=3)
    assert [t for t in out if t[1] == "us"][:2] == [("S7", "us"), ("S3", "us")]
    assert len([t for t in out if t[1] == "us"]) == 3 and ("BTC", "crypto") in out
    assert M._cap_universe(targets, edges, cap=0) == targets


def test_bar_features_fill_price_change_and_notional():
    from datetime import datetime, timezone
    from decimal import Decimal
    from agent.features import SymbolFeatures, apply_bar_features
    f = SymbolFeatures(symbol="AAPL")
    now = datetime.now(timezone.utc)
    bars = [(now, Decimal("101"), Decimal("1000")), (now, Decimal("100.5"), Decimal("900")), (now, Decimal("100.2"), Decimal("1")),
            (now, Decimal("100.1"), Decimal("1")), (now, Decimal("100.0"), Decimal("1")), (now, Decimal("100"), Decimal("1"))]
    apply_bar_features(f, bars)
    assert f.last_price == Decimal("101") and f.notional_60s_usd == Decimal("101000")
    assert f.price_change_pct_5m == Decimal("0.01")
    g = SymbolFeatures(symbol="X")
    apply_bar_features(g, [])
    assert g.last_price is None

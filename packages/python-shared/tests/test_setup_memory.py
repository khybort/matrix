"""Similar-setup memory: vector projection, neighbour summary, verdict thresholds."""

from __future__ import annotations

from decimal import Decimal

import pytest

pytestmark = pytest.mark.asyncio

from matrix_shared import setup_memory as SM
from matrix_shared.stats import wilson_bounds, wilson_lower


def _feats(**kw):
    base = {"buy_share_60s": "0.5", "n_trades_60s": 10, "notional_60s_usd": "1000", "spread_bps": "5",
            "ob_imbalance_top5": "0.5", "funding_rate": "0.0001", "oi_delta_pct_5m": "0.0",
            "price_change_pct_5m": "0.0", "n_news_1h": 0, "regime": "low/flat/neutral",
            "graph": {"direct_polarity": "0", "contextual_polarity": "0"}}
    base.update(kw)
    return base


def test_setup_vector_is_total_and_unit_scaled():
    v = SM.setup_vector({})
    assert len(v) == SM.DIM and all(-1.0 <= x <= 1.0 for x in v)
    v2 = SM.setup_vector(_feats(buy_share_60s="0.9", regime="high/up/pos", funding_rate="0.01", spread_bps="500"))
    assert len(v2) == SM.DIM and all(-1.0 <= x <= 1.0 for x in v2)
    assert v2[0] > 0.7 and v2[9] == 1.0 and v2[10] == 1.0 and v2[11] == 1.0 and v2[5] == 1.0
    # garbage never raises
    assert len(SM.setup_vector({"buy_share_60s": "nan", "n_trades_60s": None, "regime": 7})) == SM.DIM


def test_summarize_prefers_nearest_and_respects_min_sim():
    q = SM.setup_vector(_feats(buy_share_60s="0.9", oi_delta_pct_5m="0.01"))
    near = [(SM.setup_vector(_feats(buy_share_60s="0.88", oi_delta_pct_5m="0.011")), 0.01) for _ in range(12)]
    far = [(SM.setup_vector(_feats(buy_share_60s="0.1", oi_delta_pct_5m="-0.015", regime="high/down/neg")), -0.05)
           for _ in range(12)]
    st = SM.summarize(near + far, q, k=20, min_sim=0.9)
    assert st.n == 12 and st.wins == 12 and st.avg_pnl_pct > 0 and st.candidates == 24
    assert st.verdict == "good"
    st_all = SM.summarize(near + far, q, k=50, min_sim=-1.0)
    assert st_all.n == 24
    assert SM.summarize([], q) is SM.EMPTY


def test_verdict_requires_min_n_and_uses_wilson_bounds(monkeypatch):
    monkeypatch.setattr(SM, "MIN_N", 10)
    lo, hi = wilson_bounds(2, 15)  # 13% on 15 → upper ≈ 0.38 < 0.45
    bad = SM.SetupStats(15, 2, 2 / 15, lo, hi, -0.004, 0.95, 40)
    assert bad.verdict == "bad"
    lo, hi = wilson_bounds(3, 12)  # 25% on 12 → upper ≈ 0.53: still too uncertain to call bad
    assert SM.SetupStats(12, 3, 0.25, lo, hi, -0.004, 0.95, 40).verdict == "neutral"
    thin = SM.SetupStats(5, 0, 0.0, 0.0, 0.43, -0.01, 0.95, 40)
    assert thin.verdict == "neutral"
    lo, hi = wilson_bounds(7, 12)  # 58% on 12 trades → lower bound ≈ 0.32 → not good yet
    mixed = SM.SetupStats(12, 7, 7 / 12, lo, hi, 0.002, 0.95, 40)
    assert mixed.verdict == "neutral"
    lo, hi = wilson_bounds(40, 50)
    good = SM.SetupStats(50, 40, 0.8, lo, hi, 0.003, 0.95, 40)
    assert good.verdict == "good"


def test_confidence_adjustment(monkeypatch):
    monkeypatch.setattr(SM, "MIN_N", 10)
    lo, hi = wilson_bounds(40, 50)
    good = SM.SetupStats(50, 40, 0.8, lo, hi, 0.003, 0.95, 40)
    assert SM.confidence_adjustment(good, Decimal("0.95")) == Decimal("1")
    lo, hi = wilson_bounds(2, 15)
    bad = SM.SetupStats(15, 2, 2 / 15, lo, hi, -0.004, 0.95, 40)
    assert SM.confidence_adjustment(bad, Decimal("0.6")) == Decimal("0.3")
    assert SM.confidence_adjustment(SM.EMPTY, Decimal("0.6")) == Decimal("0.6")


def test_wilson_lower_is_conservative():
    assert 0.35 < wilson_lower(7, 10) < 0.7
    assert wilson_lower(70, 100) > wilson_lower(7, 10)
    assert wilson_lower(0, 0) == 0.0


async def test_similar_setups_falls_back_to_asset_class_pool(monkeypatch):
    monkeypatch.setattr(SM, "ENABLED", True)
    monkeypatch.setattr(SM, "MIN_N", 10)
    q = _feats(buy_share_60s="0.9", oi_delta_pct_5m="0.01")
    own_rows = [(SM.setup_vector(q), 0.01)] * 3
    pooled_rows = [(SM.setup_vector(q), 0.01)] * 12 + [(SM.setup_vector(_feats(regime="high/down/neg")), -0.02)] * 5

    async def fake_history(symbol, asset_class, strategy_id, side):
        return pooled_rows if symbol == "*" else own_rows
    monkeypatch.setattr(SM, "_history", fake_history)
    st = await SM.similar_setups(symbol="LSKUSDT", asset_class="crypto", strategy_id="s", side="long", features=q)
    assert st.scope == "asset_class" and st.n == 12 and st.verdict == "good"
    # enough own history → own scope wins even if the pool is bigger
    own_rows[:] = [(SM.setup_vector(q), 0.01)] * 11
    st = await SM.similar_setups(symbol="LSKUSDT", asset_class="crypto", strategy_id="s", side="long", features=q)
    assert st.scope == "symbol" and st.n == 11
    assert (await SM.similar_setups(symbol="X", asset_class="crypto", strategy_id="s", side="hold", features=q)) is SM.EMPTY

"""liq_cascade_fade: stand-down on a stale liquidation feed, the minute closes
it evaluates, what it emits for a fire (and what it only logs), the shadow-only
dispatch and the params binding."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from matrix_shared import liq_cascade as LC

from strategy.main import _instantiate_for_market
from strategy.modules.crypto import liq_cascade_fade as M
from strategy.modules.crypto.liq_cascade_fade import LiqCascadeFade
from strategy.params import instantiate

NOW = datetime(2026, 10, 20, 12, 0, 40, tzinfo=UTC)
TAU = LC.to_ms(datetime(2026, 10, 20, 11, 59, tzinfo=UTC))  # entry bar 11:59-12:00 has closed


def _fire(side="long"):
    return {"tau": TAU, "side": side, "reason": None, "L": 600_000.0, "S": 0.0, "Q": 150_000.0,
            "r5": -0.031 if side == "long" else 0.031, "sigma5": 0.002, "turnover": 4e7, "entry_px": 1.234}


def test_feed_stale():
    s = LiqCascadeFade()
    assert s.feed_stale(None, NOW)
    assert not s.feed_stale(NOW.replace(second=0) - timedelta(minutes=1), NOW)  # minute 11:59 ended 40 s ago
    assert s.feed_stale(NOW - timedelta(minutes=5), NOW)


def test_generate_stands_down_when_feed_is_stale(monkeypatch):
    async def newest():
        return datetime(2026, 1, 1, tzinfo=UTC)

    async def boom(*a, **k):
        raise AssertionError("must not read liquidations while the feed is stale")

    monkeypatch.setattr(M, "newest_covered_minute", newest)
    monkeypatch.setattr(M, "load_liquidations", boom)
    monkeypatch.setattr(M, "load_coverage", boom)
    M._state["last_tau"] = TAU - 10 * LC.MIN_MS
    assert asyncio.run(LiqCascadeFade().generate()) == []
    assert M._state["last_tau"] is None and M._state["stale"] is True  # resumes from the present


def test_taus_first_run_latest_only_then_every_closed_minute():
    s = LiqCascadeFade()
    now_ms = LC.to_ms(NOW)
    assert s.taus(None, now_ms) == [TAU]
    assert s.taus(TAU - 3 * LC.MIN_MS, now_ms) == [TAU - 2 * LC.MIN_MS, TAU - LC.MIN_MS, TAU]
    assert s.taus(TAU, now_ms) == []
    assert len(s.taus(TAU - 600 * LC.MIN_MS, now_ms)) == 31  # a long gap is capped, not replayed


def test_fire_emits_a_60m_fade_with_the_rule_inputs():
    d = LiqCascadeFade()._act("SOLUSDT", TAU, _fire("long"), NOW, tradable=True)
    assert d is not None and d.side == "long" and d.horizon_seconds == 3600
    assert d.tp_pct is None and d.sl_pct is None
    assert d.context["rule"] == "r2f.L5_60" and d.context["q99_usd"] == 150_000.0
    assert 11 < d.context["cost_bps_r2f"] < 20
    assert LiqCascadeFade()._act("SOLUSDT", TAU, _fire("short"), NOW, tradable=True).side == "short"


def test_fire_only_logged_when_untradable_or_late_or_no_fire():
    s = LiqCascadeFade()
    assert s._act("OBSCUREUSDT", TAU, _fire(), NOW, tradable=False) is None
    assert s._act("SOLUSDT", TAU, _fire(), NOW + timedelta(minutes=5), tradable=True) is None
    no = {**_fire(), "side": None, "reason": "below p99"}
    assert s._act("SOLUSDT", TAU, no, NOW, tradable=True) is None


def test_params_bind_and_shadow_band_ignored():
    s = instantiate(LiqCascadeFade, symbols=["BTCUSDT"], version=1,
                    params={"horizon_s": "3600", "max_entry_delay_s": 90, "shadow_band": {"floor_bps": 0}})
    assert s.horizon_s == 3600 and s.max_entry_delay_s == 90 and s.stale_after_s == 180


def test_shadow_only_row_runs_as_shadow():
    from matrix_shared.markets import all_markets

    crypto = next(m for m in all_markets() if m.name == "crypto")
    out = _instantiate_for_market(crypto, ["BTCUSDT"], configs={("grid", "crypto"): (1, {})},
                                  shadows={("liq_cascade_fade", "crypto"): (1, {})})
    lc = [s for s in out if s.id == "liq_cascade_fade"]
    assert len(lc) == 1 and lc[0].matrix_is_shadow is True

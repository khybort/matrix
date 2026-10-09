"""iv_inversion: the r5f.D.3 decision on a decision day (pure), the shadow-only
dispatch, and the params binding (shadow_band is not a ctor kwarg)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from matrix_shared import iv_term

from strategy.main import _instantiate_for_market
from strategy.modules.crypto.iv_inversion import IvInversion
from strategy.params import instantiate

TODAY = datetime(2026, 10, 20, tzinfo=UTC)
DAY = iv_term.DAY_MS


def _history(tail: list[float | None]) -> dict[int, float | None]:
    """200 quiet days of TERM 0 then `tail`, the last value on TODAY."""
    vals = [0.0] * 200 + tail
    end = iv_term.to_ms(TODAY)
    return {end - (len(vals) - 1 - i) * DAY: v for i, v in enumerate(vals)}


def test_no_row_today():
    h = _history([0.0])
    del h[iv_term.to_ms(TODAY)]
    assert IvInversion().decide(h, TODAY + timedelta(hours=2))[0] == "no_row"


def test_quiet_day_is_no_signal():
    state, d = IvInversion().decide(_history([0.0]), TODAY + timedelta(hours=2))
    assert state == "no_signal" and d["pct"] == 0.0


def test_window_start_enters_after_the_entry_bar_only():
    h = _history([30.0])
    s = IvInversion()
    assert s.decide(h, TODAY + timedelta(minutes=10))[0] == "wait"
    state, d = s.decide(h, TODAY + timedelta(hours=1, minutes=1))
    assert state == "enter" and d["pct"] == 1.0
    assert d["entry_ms"] == iv_term.to_ms(TODAY) + 3_600_000
    assert s.decide(h, TODAY + timedelta(hours=8))[0] == "missed"


def test_second_day_of_a_window_does_not_enter():
    assert IvInversion().decide(_history([30.0, 30.0]), TODAY + timedelta(hours=2))[0] == "no_signal"


def test_new_window_while_episode_open_is_skipped():
    # fires day -2, off day -1, fires today: the 72 h episode from day -2 is still open
    state, d = IvInversion().decide(_history([30.0, 0.0, 30.0]), TODAY + timedelta(hours=2))
    assert state == "no_signal" and d["on"]


def test_missing_term_is_no_signal():
    state, _ = IvInversion().decide(_history([None]), TODAY + timedelta(hours=2))
    assert state == "no_signal"


def test_fixed_btc_eth_and_shadow_band_ignored():
    s = instantiate(IvInversion, symbols=["SOLUSDT"], version=1,
                    params={"threshold": "0.9", "shadow_band": {"floor_bps": 0}})
    assert s.symbols == ["BTCUSDT", "ETHUSDT"] and s.threshold == 0.9 and s.horizon_s == 72 * 3600


def test_shadow_only_row_runs_as_shadow():
    from matrix_shared.markets import all_markets

    crypto = next(m for m in all_markets() if m.name == "crypto")
    out = _instantiate_for_market(crypto, ["BTCUSDT"], configs={("grid", "crypto"): (1, {})},
                                  shadows={("iv_inversion", "crypto"): (1, {})})
    iv = [s for s in out if s.id == "iv_inversion"]
    assert len(iv) == 1 and iv[0].matrix_is_shadow is True

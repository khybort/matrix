"""Post-only entry simulation: a resting order fills only when the market comes
to it, and the wait costs horizon."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from matrix_shared.edge_study import Bar
from matrix_shared.execution_study import ExecRow, post_only_entry

T0 = datetime(2026, 9, 20, tzinfo=UTC)


def _bars(rows: list[tuple[float, float, float]]) -> list[Bar]:
    # (high, low, close)
    return [Bar(T0 + timedelta(minutes=i), h, l, c) for i, (h, l, c) in enumerate(rows)]


def test_buy_limit_fills_when_price_trades_down_to_it():
    bars = _bars([(100, 100, 100), (101, 99.5, 100.8), (102, 101, 101.5)])
    e = post_only_entry(bars, 0, "long", wait_bars=1)
    assert e.filled and e.maker and e.idx == 1


def test_buy_limit_does_not_fill_in_a_market_that_only_runs_up():
    bars = _bars([(100, 100, 100), (102, 100.5, 101.5), (103, 101.6, 102.5)])
    e = post_only_entry(bars, 0, "long", wait_bars=1)
    assert not e.filled and not e.maker
    assert e.idx == 2          # caller crosses at the open of the bar after the window


def test_sell_limit_mirrors_the_buy_case():
    up = _bars([(100, 100, 100), (100.6, 99.8, 100.2)])
    assert post_only_entry(up, 0, "short", wait_bars=1).filled
    down = _bars([(100, 100, 100), (99.9, 98.0, 98.5)])
    assert not post_only_entry(down, 0, "short", wait_bars=1).filled


def test_a_longer_wait_catches_more_fills():
    bars = _bars([(100, 100, 100), (102, 100.5, 101.5), (102, 99.0, 99.5)])
    assert not post_only_entry(bars, 0, "long", wait_bars=1).filled
    assert post_only_entry(bars, 0, "long", wait_bars=2).filled


def test_edges_are_reported_not_guessed():
    bars = _bars([(100, 100, 100)])
    assert not post_only_entry(bars, 0, "long").filled
    assert not post_only_entry([], 0, "long").filled


def test_row_separates_crossing_from_skipping_when_unfilled():
    import random

    rng = random.Random(4)
    r = ExecRow("s", "crypto", n=400, fills=240)
    r.taker_net = [rng.gauss(-15.0, 40) for _ in range(400)]
    r.maker_net = [rng.gauss(-8.0, 40) for _ in range(400)]      # maker/taker spread saved
    # the passive-only arm is the SAME fills, with the unfilled ones contributing nothing
    r.passive_only_net = r.maker_net[:240] + [0.0] * 160
    row = r.as_row()
    assert row["fill_rate"] == 0.6
    assert 4.0 < row["gain_bps"] < 10.0 and row["t"] > 2
    # skipping the unfilled ones dilutes a losing arm toward zero
    assert row["passive_only_bps"] > row["postonly_net_bps"]
    assert abs(row["passive_only_bps"] - 0.6 * row["postonly_net_bps"]) < 3.0


def test_maker_fill_bar_range_is_not_scored():
    """A buy limit at 100 touched on a bar that also printed 101.5: the bar
    cannot say whether 101.5 came before or after the fill, and TP wins ties,
    so scoring the fill bar booked a take-profit the order never earned."""
    from matrix_shared.edge_study import simulate_bracket
    from matrix_shared.execution_study import maker_fill_result

    bars = [
        Bar(T0, 100.0, 100.0, 100.0, open=100.0),
        Bar(T0 + timedelta(minutes=1), 101.5, 99.9, 100.1, open=100.4),   # TP printed, then the touch
        Bar(T0 + timedelta(minutes=2), 100.3, 99.95, 100.2, open=100.1),
        Bar(T0 + timedelta(minutes=3), 100.4, 100.0, 100.3, open=100.2),
    ]
    e = post_only_entry(bars, 0, "long", wait_bars=1)
    assert e.filled and e.idx == 1
    scored_on_fill_bar = simulate_bracket(bars, e.idx, side="long", tp_pct=0.01, sl_pct=0.01,
                                          horizon_bars=4, entry_px=100.0)
    assert scored_on_fill_bar.reason == "hit_tp"      # the old, optimistic answer
    r = maker_fill_result(bars, 0, e, side="long", tp_pct=0.01, sl_pct=0.01, horizon_bars=4)
    assert r.reason == "hit_horizon" and abs(r.ret_bps - 30.0) < 1e-6

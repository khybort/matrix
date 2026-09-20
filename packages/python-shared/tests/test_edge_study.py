"""The bracket simulator is the measuring instrument of the edge study; if it
is wrong every conclusion about signal quality is wrong."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from matrix_shared.edge_study import Bar, _index_at, simulate_bracket, welch

T0 = datetime(2026, 9, 19, tzinfo=UTC)


def _bars(closes, *, highs=None, lows=None) -> list[Bar]:
    highs = highs or closes
    lows = lows or closes
    return [Bar(T0 + timedelta(minutes=i), h, l, c) for i, (c, h, l) in enumerate(zip(closes, highs, lows))]


def test_take_profit_hit_returns_the_tp_distance():
    bars = _bars([100, 100, 100], highs=[100, 101.5, 100], lows=[100, 100, 100])
    r = simulate_bracket(bars, 0, side="long", tp_pct=0.01, sl_pct=0.005, horizon_bars=2)
    assert r.reason == "hit_tp" and r.ret_bps == 100.0 and r.bars_held == 1


def test_stop_loss_hit_returns_the_negative_sl_distance():
    bars = _bars([100, 100, 100], highs=[100, 100, 100], lows=[100, 99.4, 100])
    r = simulate_bracket(bars, 0, side="long", tp_pct=0.01, sl_pct=0.005, horizon_bars=2)
    assert r.reason == "hit_sl" and r.ret_bps == -50.0


def test_take_profit_wins_ties_inside_one_bar_like_the_engine():
    bars = _bars([100, 100], highs=[100, 101.5], lows=[100, 99.0])
    assert simulate_bracket(bars, 0, side="long", tp_pct=0.01, sl_pct=0.005, horizon_bars=1).reason == "hit_tp"


def test_horizon_exit_uses_the_last_close_and_signs_by_side():
    bars = _bars([100, 100.2, 100.3])
    long = simulate_bracket(bars, 0, side="long", tp_pct=0.05, sl_pct=0.05, horizon_bars=2)
    short = simulate_bracket(bars, 0, side="short", tp_pct=0.05, sl_pct=0.05, horizon_bars=2)
    assert long.reason == "hit_horizon" and round(long.ret_bps, 1) == 30.0
    assert round(short.ret_bps, 1) == -30.0


def test_short_brackets_invert():
    bars = _bars([100, 100], highs=[100, 100], lows=[100, 98.9])
    assert simulate_bracket(bars, 0, side="short", tp_pct=0.01, sl_pct=0.005, horizon_bars=1).reason == "hit_tp"
    bars2 = _bars([100, 100], highs=[100, 100.6], lows=[100, 100])
    assert simulate_bracket(bars2, 0, side="short", tp_pct=0.01, sl_pct=0.005, horizon_bars=1).reason == "hit_sl"


def test_missing_or_edge_data_is_reported_not_guessed():
    bars = _bars([100, 101])
    assert simulate_bracket(bars, 5, side="long", tp_pct=0.01, sl_pct=0.01, horizon_bars=2).reason == "no_data"
    assert simulate_bracket(bars, 1, side="long", tp_pct=0.01, sl_pct=0.01, horizon_bars=2).reason == "no_data"
    assert simulate_bracket([], 0, side="long", tp_pct=0.01, sl_pct=0.01, horizon_bars=2).reason == "no_data"


def test_index_at_finds_the_bar_in_force():
    bars = _bars([1, 2, 3, 4])
    assert _index_at(bars, T0 + timedelta(minutes=2, seconds=30)) == 2
    assert _index_at(bars, T0) == 0
    assert _index_at(bars, T0 - timedelta(minutes=1)) == -1
    assert _index_at(bars, T0 + timedelta(hours=5)) == 3


def test_welch_detects_a_real_difference_and_ignores_noise():
    shifted = [10.0 + (1 if i % 2 else -1) for i in range(60)]   # mean 10, sd ~1
    base = [0.0 + (1 if i % 2 else -1) for i in range(60)]       # mean 0,  sd ~1
    diff, se, t = welch(shifted, base)
    assert round(diff, 6) == 10.0 and t > 10

    same_a = [1.0, -1.0] * 30
    same_b = [1.0, -1.0] * 30
    diff, se, t = welch(same_a, same_b)
    assert abs(diff) < 1e-9 and abs(t) < 1e-9

    # degenerate (no variance anywhere) must not fabricate significance
    assert welch([5.0] * 10, [0.0] * 10) == (5.0, 0.0, 0.0)
    assert welch([1.0], [2.0]) == (0.0, 0.0, 0.0)


def test_verdict_separates_paying_harmful_and_unproven():
    from matrix_shared.edge_study import verdict

    def row(**kw):
        base = {"n": 200, "edge_bps": 0.0, "t": 0.0, "side_edge_bps": 0.0, "t_side": 0.0}
        base.update(kw)
        return base

    # timing edge alone is enough, and so is directional edge alone
    assert verdict(row(edge_bps=31.2, t=5.25), cost_bps=15) == "pays"
    assert verdict(row(side_edge_bps=33.0, t_side=5.55), cost_bps=15) == "pays"
    # beats a null but not by enough to pay the spread
    assert verdict(row(edge_bps=8.0, t=3.0), cost_bps=15) == "unproven"
    # big number, no significance
    assert verdict(row(edge_bps=40.0, t=1.1), cost_bps=15) == "unproven"
    # direction loses to a coin flip → inverted, not mistuned
    assert verdict(row(side_edge_bps=-14.9, t_side=-2.46), cost_bps=15) == "harmful"
    assert verdict(row(edge_bps=-35.8, t=-2.78), cost_bps=15) == "harmful"
    # a strategy that pays on one null is kept even if the other is mildly negative
    assert verdict(row(edge_bps=31.0, t=5.0, side_edge_bps=-3.0, t_side=-0.5), cost_bps=15) == "pays"
    # too few trades to conclude anything
    assert verdict(row(n=5, edge_bps=99.0, t=9.0), cost_bps=15) == "unproven"
    assert verdict(None, cost_bps=15) == "unproven"


def test_subsample_spans_the_window_and_preserves_order():
    from matrix_shared.edge_study import subsample

    items = [{"i": i} for i in range(1000)]
    got = subsample(items, 10)
    assert len(got) == 10
    assert got[0]["i"] == 0 and got[-1]["i"] >= 890          # last decile represented
    assert [g["i"] for g in got] == sorted(g["i"] for g in got)
    assert subsample(items, 0) is items                      # disabled
    assert subsample(items[:5], 10) == items[:5]             # smaller than cap


def test_two_sided_p_matches_known_normal_quantiles():
    from matrix_shared.edge_study import two_sided_p

    assert abs(two_sided_p(1.96) - 0.05) < 0.002
    assert abs(two_sided_p(2.576) - 0.01) < 0.002
    assert two_sided_p(0.0) == 1.0
    assert two_sided_p(-6.0) < 1e-8          # sign does not matter


def test_benjamini_hochberg_controls_the_false_discovery_rate():
    from matrix_shared.edge_study import benjamini_hochberg

    # one strong signal among noise survives; the marginal ones do not
    ps = [0.0001, 0.20, 0.35, 0.60, 0.04]
    keep = benjamini_hochberg(ps, q=0.05)
    assert keep[0] is True and keep[4] is False and not any(keep[1:4])
    # several genuinely small p-values all survive
    assert benjamini_hochberg([0.001, 0.002, 0.003], q=0.05) == [True, True, True]
    # nothing survives when everything is noise
    assert benjamini_hochberg([0.4, 0.5, 0.9], q=0.05) == [False, False, False]
    assert benjamini_hochberg([]) == []


def test_both_nulls_are_reported_and_either_can_establish_edge():
    import random

    from matrix_shared.edge_study import StrategyEdge

    rng = random.Random(5)

    def noisy(mean: float, n: int = 400) -> list[float]:
        return [rng.gauss(mean, 60) for _ in range(n)]

    # times entries well, no directional view: beats the random-time control,
    # indistinguishable from a coin-flipped side at the same moments
    e = StrategyEdge("timer", "crypto", n=400)
    e.treatment, e.control, e.control_side = noisy(30), noisy(0), noisy(29)
    row = e.as_row()
    assert row["edge_bps"] > 15 and row["t"] > 3
    assert abs(row["t_side"]) < 2

    # picks direction well but any entry time would do
    d = StrategyEdge("picker", "crypto", n=400)
    d.treatment, d.control, d.control_side = noisy(30), noisy(29), noisy(0)
    row = d.as_row()
    assert abs(row["t"]) < 2
    assert row["side_edge_bps"] > 15 and row["t_side"] > 3

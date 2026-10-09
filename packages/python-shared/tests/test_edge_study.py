"""The bracket simulator is the measuring instrument of the edge study; if it
is wrong every conclusion about signal quality is wrong."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from matrix_shared.edge_study import Bar, _index_at, simulate_bracket, welch

T0 = datetime(2026, 9, 19, tzinfo=UTC)


def _bars(closes, *, highs=None, lows=None, opens=None) -> list[Bar]:
    highs = highs or closes
    lows = lows or closes
    opens = opens or closes
    return [Bar(T0 + timedelta(minutes=i), h, l, c, o)
            for i, (c, h, l, o) in enumerate(zip(closes, highs, lows, opens))]


def test_take_profit_hit_returns_the_tp_distance():
    # entry at bar 0's open (100); bar 0 itself is part of the scored path
    bars = _bars([100, 100, 100], highs=[100.5, 101.5, 100], lows=[100, 100, 100])
    r = simulate_bracket(bars, 0, side="long", tp_pct=0.01, sl_pct=0.005, horizon_bars=2)
    assert r.reason == "hit_tp" and r.ret_bps == 100.0 and r.bars_held == 2


def test_stop_loss_hit_returns_the_negative_sl_distance():
    bars = _bars([100, 100, 100], highs=[100, 100, 100], lows=[100, 99.4, 100])
    r = simulate_bracket(bars, 0, side="long", tp_pct=0.01, sl_pct=0.005, horizon_bars=2)
    assert r.reason == "hit_sl" and r.ret_bps == -50.0


def test_the_entry_bar_after_its_open_is_scored():
    bars = _bars([100, 100], highs=[101.5, 100], lows=[100, 100])
    r = simulate_bracket(bars, 0, side="long", tp_pct=0.01, sl_pct=0.005, horizon_bars=1)
    assert r.reason == "hit_tp" and r.bars_held == 1


def test_take_profit_wins_ties_inside_one_bar_like_the_engine():
    bars = _bars([100, 100], highs=[101.5, 100], lows=[99.0, 100])
    assert simulate_bracket(bars, 0, side="long", tp_pct=0.01, sl_pct=0.005, horizon_bars=1).reason == "hit_tp"


def test_horizon_exit_enters_at_the_open_exits_at_the_last_close_and_signs_by_side():
    bars = _bars([100.2, 100.3, 101], opens=[100, 100.2, 100.3])
    long = simulate_bracket(bars, 0, side="long", tp_pct=0.05, sl_pct=0.05, horizon_bars=2)
    short = simulate_bracket(bars, 0, side="short", tp_pct=0.05, sl_pct=0.05, horizon_bars=2)
    assert long.reason == "hit_horizon" and round(long.ret_bps, 1) == 30.0 and long.bars_held == 2
    assert round(short.ret_bps, 1) == -30.0


def test_short_brackets_invert():
    bars = _bars([100, 100], highs=[100, 100], lows=[98.9, 100])
    assert simulate_bracket(bars, 0, side="short", tp_pct=0.01, sl_pct=0.005, horizon_bars=1).reason == "hit_tp"
    bars2 = _bars([100, 100], highs=[100.6, 100], lows=[100, 100])
    assert simulate_bracket(bars2, 0, side="short", tp_pct=0.01, sl_pct=0.005, horizon_bars=1).reason == "hit_sl"


def test_missing_or_edge_data_is_reported_not_guessed():
    bars = _bars([100, 101])
    assert simulate_bracket(bars, 5, side="long", tp_pct=0.01, sl_pct=0.01, horizon_bars=2).reason == "no_data"
    assert simulate_bracket(bars, -1, side="long", tp_pct=0.01, sl_pct=0.01, horizon_bars=2).reason == "no_data"
    assert simulate_bracket(bars, 0, side="long", tp_pct=0.01, sl_pct=0.01, horizon_bars=0).reason == "no_data"
    assert simulate_bracket([], 0, side="long", tp_pct=0.01, sl_pct=0.01, horizon_bars=2).reason == "no_data"


def test_entry_px_overrides_the_open():
    bars = _bars([100, 100], highs=[100, 100.6], lows=[99, 100], opens=[100, 100])
    r = simulate_bracket(bars, 0, side="long", tp_pct=0.01, sl_pct=0.05, horizon_bars=2, entry_px=99.6)
    assert r.reason == "hit_tp"


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


def _sig(minute: int, *, symbol="UAIUSDT", side="short", horizon=3600, sid="momentum_xs"):
    return {
        "strategy_id": sid, "asset_class": "crypto", "symbol": symbol, "side": side,
        "horizon_seconds": horizon, "generated_at": T0 + timedelta(minutes=minute),
    }


def test_re_emissions_of_one_bet_collapse_into_one_episode():
    """momentum_xs v1 re-emitted the same (symbol, side) every ~90 s for three
    hours on 2026-09-13; counted as 1 764 independent samples they produced
    't=6'. The wallet can hold that bet once, so the study must count it once."""
    from matrix_shared.edge_study import one_per_episode

    burst = [_sig(m) for m in range(0, 180, 2)]            # 90 rows, 3 h, 1 h horizon
    kept = one_per_episode(burst)
    assert [k["generated_at"] for k in kept] == [T0, T0 + timedelta(minutes=60), T0 + timedelta(minutes=120)]

    # a different symbol, side or strategy is a different bet
    mixed = [_sig(0), _sig(1, symbol="LSKUSDT"), _sig(2, side="long"), _sig(3, sid="oi_delta"), _sig(4)]
    assert len(one_per_episode(mixed)) == 4


def test_entry_is_the_first_bar_starting_after_the_signal_plus_latency():
    """Bar ts is the bar's start. No part of the scored path may precede the
    signal: the bar in force at generated_at began before it, so the trade
    enters at the open of the NEXT bar (or later, by the engine's latency)."""
    from matrix_shared.edge_study import ENTRY_LATENCY, entry_index

    assert ENTRY_LATENCY == timedelta(seconds=4)
    bars = _bars([1, 2, 3, 4, 5])
    assert entry_index(bars, T0 + timedelta(minutes=2, seconds=30)) == 3
    assert entry_index(bars, T0 + timedelta(minutes=2)) == 3                 # 2:04 > bar 2's start
    assert entry_index(bars, T0 + timedelta(minutes=1, seconds=56)) == 2     # exactly 2:00
    assert entry_index(bars, T0 + timedelta(minutes=1, seconds=57)) == 3
    assert entry_index(bars, T0 - timedelta(seconds=30)) == 0
    assert entry_index(bars, T0 + timedelta(minutes=4, seconds=1)) == -1     # no later bar


def test_the_move_that_fired_the_signal_is_not_credited():
    """A breakout fires at 10:00:40 on a jump at 10:00:20. The old rule entered
    at the close of the 09:59 bar and scored from the 10:00 bar, so the jump
    itself paid the take-profit. Entering at the 10:01 open, it does not."""
    from matrix_shared.edge_study import entry_index

    closes = [100, 100, 102, 102, 102]
    bars = _bars(closes, highs=[100, 100, 102.2, 102.1, 102.1], lows=[100, 100, 100, 101.9, 101.9],
                 opens=[100, 100, 100, 102, 102])
    signal = T0 + timedelta(minutes=2, seconds=40)
    i = entry_index(bars, signal)
    assert i == 3 and bars[i].ts >= signal
    r = simulate_bracket(bars, i, side="long", tp_pct=0.01, sl_pct=0.01, horizon_bars=2)
    assert r.reason == "hit_horizon" and abs(r.ret_bps) < 1e-9
    # the pre-fix rule, for the record: entry at bar 1's close, scored from bar 2
    old_entry = bars[1].close
    assert (bars[2].high - old_entry) / old_entry >= 0.01


def test_beating_a_losing_null_is_not_paying():
    """The wallet earns the treatment's level, not its lead over the control."""
    from matrix_shared.edge_study import verdict

    row = {"n": 200, "edge_bps": 30.0, "t": 5.0, "side_edge_bps": 0.0, "t_side": 0.0}
    assert verdict({**row, "gross_bps": 40.0}, cost_bps=15) == "pays"
    assert verdict({**row, "gross_bps": 5.0}, cost_bps=15) == "unproven"   # control was -25


def test_episode_groups_keep_every_fill_and_sum_its_dollars():
    """Fills of one re-emitted bet are one sample whose PnL is their sum: the
    wallet really held them, so dropping them would misstate the dollars."""
    from matrix_shared.edge_study import episode_groups, episode_pnls, one_per_episode

    fills = [{**_sig(m), "pnl_usd": p} for m, p in [(0, -1.0), (10, -1.0), (20, -1.0), (70, 2.0)]]
    groups = episode_groups(fills)
    assert [len(g) for g in groups] == [3, 1]
    assert episode_pnls(fills) == [-3.0, 2.0]
    assert one_per_episode(fills) == [g[0] for g in groups]


def test_sample_episodes_reports_raw_count_beside_the_episodes():
    from matrix_shared.edge_study import sample_episodes

    rows = [_sig(m) for m in range(0, 180, 2)] + [_sig(0, sid="oi_delta")]
    rows.sort(key=lambda r: r["generated_at"])
    sampled, n_raw = sample_episodes(rows, cap=2)
    assert n_raw == {("momentum_xs", "crypto"): 90, ("oi_delta", "crypto"): 1}
    assert sum(1 for r in sampled if r["strategy_id"] == "momentum_xs") == 2   # 3 episodes, capped


@pytest.mark.asyncio
async def test_a_single_strategy_run_is_corrected_for_the_whole_family(monkeypatch):
    """The cached gate studies one strategy at a time. Corrected for m=1, BHY
    was a bare p<0.05 and the deflated Sharpe was not deflated; it must be
    corrected for every strategy the book is testing."""
    from matrix_shared import edge_study as E

    bars = _bars([100 + (i % 7) * 0.3 for i in range(400)])
    sigs = [
        {**_sig(30 + 3 * k, symbol="X", side="long", horizon=60), "tp_pct": 0.002, "sl_pct": 0.002,
         "filled": False, "pnl_pct": None}
        for k in range(100)
    ]

    async def cands(days, sid):
        return sigs

    async def load_bars(syms, ac, since):
        return {"X": bars}

    async def no_carries(days, sid):
        return []

    monkeypatch.setattr(E, "_load_candidates", cands)
    monkeypatch.setattr(E, "_load_carry_fills", no_carries)
    monkeypatch.setattr(E, "_load_bars", load_bars)
    monkeypatch.setattr(E.Registry, "save", lambda self, path=None: None)
    alone = (await E.run_edge_study(days=1, strategy_id="momentum_xs", family=1))[0]
    book = (await E.run_edge_study(days=1, strategy_id="momentum_xs", family=13))[0]
    assert alone["family"] == 1 and book["family"] == 13
    if alone["dsr"] is not None and book["dsr"] is not None:
        assert book["dsr"] <= alone["dsr"]
    assert book["significant"] <= alone["significant"]


def test_a_stale_entry_bar_is_unscorable_not_entered_hours_early():
    """A hole after the signal must not let the entry land hours later: bars
    stop at minute 3 and resume at minute 300, so a signal at 2:30 has no
    tradeable price within MAX_ENTRY_AGE."""
    from matrix_shared.edge_study import entry_index

    bars = [Bar(T0 + timedelta(minutes=m), 100, 100, 100) for m in (0, 1, 2, 300, 301, 302)]
    assert entry_index(bars, T0 + timedelta(minutes=2, seconds=30)) == -1    # next bar 4h later
    assert entry_index(bars, T0 + timedelta(minutes=299, seconds=30)) == 3
    assert entry_index(bars, T0 + timedelta(minutes=300, seconds=30)) == 4
    assert entry_index(bars, T0 + timedelta(minutes=0, seconds=30)) == 1


def test_a_window_with_a_hole_is_not_simulated_across_it():
    """bist_volume_breakout's 244 stale entries were all +300 bps take-profits:
    the bracket ran across a gap into prices the strategy had already seen."""
    from matrix_shared.edge_study import contiguous

    bars = [Bar(T0 + timedelta(minutes=m), h, 100, 100)
            for m, h in [(0, 100), (1, 100), (2, 100), (240, 110), (241, 110)]]
    r = simulate_bracket(bars, 0, side="long", tp_pct=0.03, sl_pct=0.03, horizon_bars=4)
    assert r.reason == "no_data"
    assert contiguous(bars, 0, 2) and not contiguous(bars, 0, 3)
    # an exit before the hole is still scored
    early = [Bar(T0 + timedelta(minutes=m), h, 100, 100)
             for m, h in [(0, 100), (1, 104), (2, 100), (240, 110)]]
    assert simulate_bracket(early, 0, side="long", tp_pct=0.03, sl_pct=0.03, horizon_bars=3).reason == "hit_tp"

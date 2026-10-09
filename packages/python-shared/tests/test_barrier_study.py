"""Volatility scaling of the triple barrier (López de Prado): the barrier must
mean the same thing in every regime."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

from matrix_shared.barrier_study import BarrierRow, horizon_vol, realised_vol
from matrix_shared.edge_study import Bar

T0 = datetime(2026, 9, 20, tzinfo=UTC)


def _series(closes: list[float]) -> list[Bar]:
    return [Bar(T0 + timedelta(minutes=i), c, c, c) for i, c in enumerate(closes)]


def test_realised_vol_is_zero_without_enough_history():
    assert realised_vol(_series([100] * 5), 4) == 0.0


def test_realised_vol_grows_with_dispersion():
    calm = _series([100 * (1 + 0.0001 * (-1) ** i) for i in range(80)])
    wild = _series([100 * (1 + 0.01 * (-1) ** i) for i in range(80)])
    v_calm, v_wild = realised_vol(calm, 79), realised_vol(wild, 79)
    assert 0 < v_calm < v_wild
    assert v_wild > 10 * v_calm


def test_horizon_vol_scales_with_square_root_of_time():
    assert math.isclose(horizon_vol(0.001, 100), 0.01, rel_tol=1e-9)
    assert math.isclose(horizon_vol(0.001, 1), 0.001, rel_tol=1e-9)
    assert horizon_vol(0.001, 0) == 0.001          # floors at one bar


def test_row_picks_the_multiple_with_the_best_net_and_reports_the_gain():
    r = BarrierRow("s", "crypto", n=100)
    r.tp_in_sigma = [3.0] * 100                    # current TP is 3σ away: unreachable
    r.current_net_bps = [5.0] * 100                # +5 gross → −10 net at 15 bps cost
    r.by_m = {0.5: [30.0] * 100, 1.0: [20.0] * 100, 2.0: [5.0] * 100}
    r.horizon_share = {0.5: 20, 1.0: 50, 2.0: 90}
    row = r.as_row(cost_bps=15.0)
    assert row["tp_sigma"] == 3.0
    assert row["current_net_bps"] == -10.0
    assert row["best_m"] == 0.5 and row["best_net_bps"] == 15.0
    assert row["best_horizon_share"] == 0.2
    assert row["by_m"][2.0] == -10.0


def test_signed_return_is_directional_and_bounded_by_the_series():
    from matrix_shared.barrier_study import signed_return_bps

    bars = _series([100, 101, 102])
    # in at bar 0 (synthetic: no open, so its close), out at the close of bar 2
    assert signed_return_bps(bars, 0, "long", 3) == 200.0
    assert signed_return_bps(bars, 0, "short", 3) == -200.0
    assert signed_return_bps(bars, 0, "long", 5) is None      # past the end
    # with an open, the entry is the open: nothing before the entry bar counts
    opened = [Bar(T0 + timedelta(minutes=i), c, c, c, o) for i, (o, c) in enumerate([(99, 100), (100, 101)])]
    assert round(signed_return_bps(opened, 0, "long", 2), 6) == round((101 - 99) / 99 * 10_000, 6)
    assert signed_return_bps(bars, -1, "long", 1) is None

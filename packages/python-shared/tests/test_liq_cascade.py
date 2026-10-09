"""matrix_shared.liq_cascade: the r2f rule (real-liquidation cascade fade)."""

from __future__ import annotations

import math

from matrix_shared import liq_cascade as L

M = L.MIN_MS
T0 = 1_791_504_000_000  # 2026-10-09 00:00 UTC
TAU = T0 + 8 * L.DAY_MS + 30 * M  # 8 days of history, mid-hour


def _covered(start=T0, end=TAU + 10 * M):
    return set(range(start, end, M))


def _quiet_liq():
    """One $5k long liquidation every 12 hours: < 1 % of windows are non-zero, so Q = 0."""
    return L.bucket((t, "long", 5_000.0) for t in range(T0, TAU - 10 * M, 12 * L.H_MS))


def _prices(drop: float = 0.0, turnover=1e4):
    """Bar-end -> close: a slow +-0.1 % zigzag, then a `drop` over the last 5 bars to TAU."""
    closes, to = {}, {}
    p = 100.0
    for i, e in enumerate(range(TAU - 2 * L.DAY_MS, TAU + 300 * M, M)):
        if TAU - 5 * M < e <= TAU:
            p *= math.exp(drop / 5)
        else:
            p *= 1.001 if i % 2 else 0.999
        closes[e] = p
        to[e] = turnover
    return closes, to


def _cascade(liq, usd=500_000.0, side="long"):
    for k in range(1, 5):
        liq.setdefault(TAU - k * M, [0.0, 0.0])[0 if side == "long" else 1] += usd / 4
    return liq


def test_bucket_and_window_sums():
    liq = L.bucket([(T0 + 1_000, "long", 10.0), (T0 + 59_000, "short", 5.0), (T0 + 61_000, "long", 1.0)])
    assert liq == {T0: [10.0, 5.0], T0 + M: [1.0, 0.0]}
    assert L.window_sums(liq, T0 + 5 * M) == (11.0, 5.0)
    assert L.window_sums(liq, T0 + 6 * M) == (1.0, 0.0)  # T0's minute left the window


def test_threshold_nearest_rank_with_zero_windows():
    cov = _covered()
    assert L.threshold(_quiet_liq(), cov, TAU) == 0.0  # > 99 % of windows are zero
    # every window end carries a long liquidation: Q = the 99th percentile of the long side
    dense = L.bucket((t, "long", float(1 + (t // M) % 100)) for t in range(T0, TAU, M))
    q = L.threshold(dense, cov, TAU)
    assert q is not None and q > 0
    assert L.threshold(dense, _covered(TAU - 5 * L.DAY_MS), TAU) is None  # < 6 days covered


def test_long_cascade_with_dump_fades_long():
    closes, to = _prices(drop=-0.05, turnover=2e4)  # 2e4 x 1440 = $28.8M / 24h
    d = L.evaluate(TAU, _cascade(_quiet_liq()), _covered(), closes, to)
    assert d["side"] == "long", d
    assert d["L"] >= 500_000 - 1 and d["r5"] < -0.02 and d["turnover"] > L.MIN_TURNOVER_USD


def test_short_squeeze_fades_short():
    closes, to = _prices(drop=+0.05, turnover=2e4)
    assert L.evaluate(TAU, _cascade(_quiet_liq(), side="short"), _covered(), closes, to)["side"] == "short"


def test_each_leg_is_required():
    closes, to = _prices(drop=-0.05, turnover=2e4)
    cov = _covered()
    assert L.evaluate(TAU, _quiet_liq(), cov, closes, to)["reason"] == "below floor"
    flat, _ = _prices(drop=0.0, turnover=2e4)
    assert L.evaluate(TAU, _cascade(_quiet_liq()), cov, flat, to)["reason"] == "no displacement"
    up, _ = _prices(drop=+0.05, turnover=2e4)  # long liquidations but price UP: not this rule
    assert L.evaluate(TAU, _cascade(_quiet_liq()), cov, up, to)["side"] is None
    thin, thin_to = _prices(drop=-0.05, turnover=100.0)
    assert L.evaluate(TAU, _cascade(_quiet_liq()), cov, thin, thin_to)["reason"] == "illiquid"
    gap = cov - {TAU - 3 * M}
    assert L.evaluate(TAU, _cascade(_quiet_liq()), gap, closes, to)["reason"] == "window not covered"
    assert L.evaluate(TAU, _cascade(_quiet_liq()), _covered(TAU - 3 * L.DAY_MS), closes, to)["reason"] == "< 6 days covered"


def test_below_trailing_p99_does_not_fire():
    closes, to = _prices(drop=-0.05, turnover=2e4)
    busy = L.bucket((t, "long", 400_000.0) for t in range(T0, TAU - 10 * M, M))  # every window ~ $2M
    assert L.evaluate(TAU, _cascade(busy, usd=600_000.0), _covered(), closes, to)["reason"] == "below p99"


def test_displacement_uses_round2_sigma():
    closes, _ = _prices(drop=-0.05)
    r5, sig = L.displacement(closes, TAU)
    assert abs(r5 - (-0.05)) < 1e-9
    assert sig is not None and sig < 0.005  # the zigzag, not the dump (window ends at TAU - 5m)
    assert L.displacement({}, TAU) == (None, None)


def test_candidates_only_windows_over_the_floor():
    liq = _cascade(_quiet_liq())
    c = L.candidates(liq, _covered(), TAU - 30 * M, TAU + 30 * M)
    assert TAU in c and all(max(L.window_sums(liq, t)) >= L.FLOOR_USD for t in c)
    assert c == sorted(c)


def test_cost_matches_round2_event_cost():
    # ~$14M/day (round 2 H8b panel median): 11 bps taker + 2.2 x 2.8 bps
    assert 16.5 < L.cost_bps(14e6) < 18.0
    assert L.cost_bps(1e9) < L.cost_bps(2e6)

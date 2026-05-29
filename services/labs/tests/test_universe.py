"""Pure-function unit tests for the universe manager (no DB).

Symbols are TEST_-prefixed per project convention so nothing here can collide
with live universe rows.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from labs.universe import (
    ScoredSymbol,
    UniverseConfig,
    _pct_rank,
    _plan_flips,
    _score_pool,
    _shrink_edge,
)

NOW = datetime(2026, 5, 29, 12, 0, 0, tzinfo=UTC)


def _cfg(**over) -> UniverseConfig:
    base = dict(
        target_active_n=3,
        enter_floor=0.70,
        exit_floor=0.40,
        min_hold_seconds=3600,
        max_churn_per_scan=10,
        min_liquidity_usd=1_000_000,
        weights={"L": 0.35, "V": 0.15, "M": 0.10, "E": 0.30, "U": 0.10},
    )
    base.update(over)
    return UniverseConfig(**base)


def _S(sym: str, score: float, liq: float) -> ScoredSymbol:
    return ScoredSymbol(symbol=sym, score=score, liquidity=liq, components={})


# ── _pct_rank ────────────────────────────────────────────────────────────────


def test_pct_rank_monotonic():
    r = _pct_rank([10, 20, 30, 40, 50])
    assert r[0] == 0.0 and r[-1] == 1.0
    assert r == sorted(r)


def test_pct_rank_ties_share_mean():
    r = _pct_rank([5, 5, 5, 5])
    assert all(abs(x - 0.5) < 1e-9 for x in r)


def test_pct_rank_singleton_and_empty():
    assert _pct_rank([7]) == [0.5]
    assert _pct_rank([]) == []


# ── _shrink_edge ─────────────────────────────────────────────────────────────


def test_shrink_edge_zero_sample_is_neutral():
    assert _shrink_edge(0, 0.9) == 0.5
    assert _shrink_edge(0, -0.9) == 0.5


def test_shrink_edge_monotonic_in_n():
    # more evidence of a positive edge → closer to 1 (further from neutral 0.5)
    low_n = _shrink_edge(5, 0.6)
    high_n = _shrink_edge(500, 0.6)
    assert 0.5 < low_n < high_n < 1.0


def test_shrink_edge_negative_below_neutral():
    assert _shrink_edge(200, -0.5) < 0.5


def test_shrink_edge_clamped():
    assert 0.0 <= _shrink_edge(10_000, -1.0) <= 1.0
    assert 0.0 <= _shrink_edge(10_000, 1.0) <= 1.0


# ── _score_pool ──────────────────────────────────────────────────────────────


def _row(sym, last, turnover, hi, lo, mom, fr):
    return {
        "symbol": sym, "last_price": last, "turnover24h": turnover,
        "high24h": hi, "low24h": lo, "price24h_pct": mom, "funding_rate": fr,
    }


def test_score_pool_drops_below_liquidity_floor():
    cfg = _cfg(min_liquidity_usd=1_000_000)
    pool = [
        _row("TEST_BIG", 100, 5_000_000, 110, 90, 0.05, 0.001),
        _row("TEST_DUST", 1, 10, 2, 0.5, 0.9, 0.05),
    ]
    syms = {s.symbol for s in _score_pool(pool, {}, cfg)}
    assert syms == {"TEST_BIG"}


def test_score_pool_higher_liquidity_outranks_when_else_equal():
    cfg = _cfg(min_liquidity_usd=0)
    pool = [
        _row("TEST_A", 100, 9_000_000, 105, 95, 0.03, 0.001),
        _row("TEST_B", 100, 1_000_000, 105, 95, 0.03, 0.001),
    ]
    scored = _score_pool(pool, {}, cfg)
    assert scored[0].symbol == "TEST_A"


def test_score_pool_edge_breaks_tie():
    cfg = _cfg(min_liquidity_usd=0)
    pool = [
        _row("TEST_A", 100, 5_000_000, 105, 95, 0.03, 0.001),
        _row("TEST_B", 100, 5_000_000, 105, 95, 0.03, 0.001),
    ]
    # identical market inputs → equal L/V/M/U ranks; realized edge decides
    scored = _score_pool(pool, {"TEST_A": 0.9, "TEST_B": 0.1}, cfg)
    by = {s.symbol: s.score for s in scored}
    assert by["TEST_A"] > by["TEST_B"]


# ── _plan_flips (hysteresis / floor / hold / churn) ──────────────────────────


def test_plan_enter_respects_floor_and_target():
    cfg = _cfg(target_active_n=2, enter_floor=0.70)
    scored = [_S("TEST_A", 0.9, 5e6), _S("TEST_B", 0.8, 5e6),
              _S("TEST_C", 0.75, 5e6), _S("TEST_D", 0.5, 5e6)]
    enter, exit_ = _plan_flips(scored, {}, cfg, NOW)
    assert enter == ["TEST_A", "TEST_B"]  # capped at target_n=2; D below enter_floor
    assert exit_ == []


def test_plan_hysteresis_band_no_flip():
    cfg = _cfg(enter_floor=0.70, exit_floor=0.40)
    scored = [_S("TEST_A", 0.5, 5e6)]  # between the bands
    _, exit_ = _plan_flips(scored, {"TEST_A": NOW - timedelta(hours=10)}, cfg, NOW)
    assert exit_ == []                       # active stays active
    enter, _ = _plan_flips(scored, {}, cfg, NOW)
    assert enter == []                       # inactive stays inactive


def test_plan_exit_when_faded_and_old():
    cfg = _cfg(exit_floor=0.40, min_hold_seconds=3600)
    scored = [_S("TEST_A", 0.2, 5e6)]
    _, exit_ = _plan_flips(scored, {"TEST_A": NOW - timedelta(hours=10)}, cfg, NOW)
    assert exit_ == ["TEST_A"]


def test_plan_min_hold_blocks_score_exit():
    cfg = _cfg(exit_floor=0.40, min_hold_seconds=3600)
    scored = [_S("TEST_A", 0.2, 5e6)]  # faded but only 10 min old
    _, exit_ = _plan_flips(scored, {"TEST_A": NOW - timedelta(minutes=10)}, cfg, NOW)
    assert exit_ == []


def test_plan_liquidity_floor_overrides_min_hold():
    cfg = _cfg(exit_floor=0.40, min_hold_seconds=3600, min_liquidity_usd=1_000_000)
    scored = [_S("TEST_A", 0.95, 500_000)]  # great score but below floor, brand new
    _, exit_ = _plan_flips(scored, {"TEST_A": NOW - timedelta(minutes=1)}, cfg, NOW)
    assert exit_ == ["TEST_A"]


def test_plan_gone_from_pool_is_dropped():
    cfg = _cfg()
    _, exit_ = _plan_flips([], {"TEST_GONE": NOW - timedelta(days=1)}, cfg, NOW)
    assert exit_ == ["TEST_GONE"]


def test_plan_churn_cap_limits_activations():
    cfg = _cfg(target_active_n=100, enter_floor=0.50, max_churn_per_scan=2,
               min_liquidity_usd=0)
    scored = [_S(f"TEST_{i}", 0.9, 5e6) for i in range(5)]
    enter, _ = _plan_flips(scored, {}, cfg, NOW)
    assert len(enter) == 2

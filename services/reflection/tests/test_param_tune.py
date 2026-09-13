"""Pure unit tests for reflection.mutate.rule_propose_param_tune.

No DB, no LLM. Verifies the param_tune path for deterministic strategies
(grid/dca/oi_delta). matrix_agent is excluded from this path.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from reflection.metrics import StrategyMetrics
from reflection.mutate import rule_propose_param_tune


def _metrics(
    strategy_id: str = "grid",
    n_outcomes: int = 50,
    avg_score: Decimal = Decimal("-0.08"),
    win_rate: Decimal = Decimal("0.30"),
    total_pnl_usd: Decimal = Decimal("-12.0"),
) -> StrategyMetrics:
    return StrategyMetrics(
        strategy_id=strategy_id,
        version=1,
        n_outcomes=n_outcomes,
        avg_score=avg_score,
        win_rate=win_rate,
        total_pnl_usd=total_pnl_usd,
        by_symbol={},
    )


def _grid_params() -> dict:
    return {
        "n_grids": 10,
        "price_band_pct": "0.02",
        "horizon_s": 300,
        "tp_pct": "0.010",
        "sl_pct": "0.015",
    }


def _dca_params() -> dict:
    return {"interval_minutes": 60}


def _oi_delta_params() -> dict:
    return {
        "oi_threshold_pct": "0.015",
        "horizon_s": 300,
        "tp_pct": "0.015",
        "sl_pct": "0.0075",
    }


# ---------------------------------------------------------------------------
# Grid tests
# ---------------------------------------------------------------------------

def test_param_tune_grid_widens_band_on_poor_winrate():
    """Low win_rate (<0.5) should push a grid param upward (widen/lengthen).

    grid knobs (sorted): ['horizon_s', 'price_band_pct', 'sl_pct', 'tp_pct'] → 4 knobs.
    n=52 → 52 % 4 = 0 → 'horizon_s'.
    """
    m = _metrics(strategy_id="grid", win_rate=Decimal("0.30"), n_outcomes=52)
    draft = rule_propose_param_tune("grid", _grid_params(), m)
    assert draft is not None
    assert draft.proposal_type == "param_tune"
    assert draft.source == "rule"

    after_horizon = draft.after_params.get("horizon_s")
    assert after_horizon is not None
    assert int(after_horizon) > 300, "horizon_s should increase (widen) when win_rate < 0.5"


def test_param_tune_grid_price_band_knob():
    """grid knobs: ['horizon_s', 'price_band_pct', 'sl_pct', 'tp_pct'].
    n=53 → 53 % 4 = 1 → 'price_band_pct'.
    """
    m = _metrics(strategy_id="grid", win_rate=Decimal("0.25"), n_outcomes=53)
    draft = rule_propose_param_tune("grid", _grid_params(), m)
    assert draft is not None
    after_band = Decimal(draft.after_params["price_band_pct"])
    assert after_band > Decimal("0.02"), "price_band_pct should increase with poor win_rate"


def test_param_tune_grid_tightens_on_high_winrate():
    """High win_rate + negative avg_score (but tune still triggered) → tighten."""
    m = _metrics(
        strategy_id="grid",
        win_rate=Decimal("0.60"),
        avg_score=Decimal("-0.08"),
        total_pnl_usd=Decimal("2.0"),
        n_outcomes=52,
    )
    draft = rule_propose_param_tune("grid", _grid_params(), m)
    assert draft is not None
    after_horizon = int(draft.after_params["horizon_s"])
    assert after_horizon < 300, "horizon_s should decrease when profitable with good win_rate"


def test_param_tune_grid_widens_when_winrate_ok_but_pnl_negative():
    """High win_rate but negative PnL → losers bigger than winners → widen."""
    m = _metrics(
        strategy_id="grid",
        win_rate=Decimal("0.60"),
        avg_score=Decimal("-0.08"),
        total_pnl_usd=Decimal("-12.0"),
        n_outcomes=52,
    )
    draft = rule_propose_param_tune("grid", _grid_params(), m)
    assert draft is not None
    after_horizon = int(draft.after_params["horizon_s"])
    assert after_horizon > 300


# ---------------------------------------------------------------------------
# DCA tests
# ---------------------------------------------------------------------------

def test_param_tune_dca_lengthens_interval_on_poor_winrate():
    """DCA with poor win_rate should lengthen interval_minutes."""
    m = _metrics(strategy_id="dca", win_rate=Decimal("0.30"), n_outcomes=50)
    draft = rule_propose_param_tune("dca", _dca_params(), m)
    assert draft is not None
    assert draft.proposal_type == "param_tune"
    after = int(draft.after_params["interval_minutes"])
    assert after > 60, "interval_minutes should increase with poor win_rate"


def test_param_tune_dca_shortens_interval_on_good_winrate():
    """High win_rate + mildly bad score but positive PnL → shorten cadence."""
    m = _metrics(
        strategy_id="dca",
        win_rate=Decimal("0.55"),
        avg_score=Decimal("-0.06"),
        total_pnl_usd=Decimal("2.0"),
        n_outcomes=50,
    )
    draft = rule_propose_param_tune("dca", _dca_params(), m)
    assert draft is not None
    after = int(draft.after_params["interval_minutes"])
    assert after < 60, "interval_minutes should decrease when profitable with good win_rate"


def test_param_tune_dca_clamps_at_min():
    """At minimum interval but still losing PnL → lengthen (not clamp to None)."""
    params = {"interval_minutes": 15}
    m = _metrics(strategy_id="dca", win_rate=Decimal("0.55"), avg_score=Decimal("-0.06"), n_outcomes=50)
    draft = rule_propose_param_tune("dca", params, m)
    assert draft is not None
    assert int(draft.after_params["interval_minutes"]) == 30


def test_param_tune_dca_clamps_at_max():
    """interval_minutes must not exceed 240."""
    params = {"interval_minutes": 240}
    m = _metrics(strategy_id="dca", win_rate=Decimal("0.30"), avg_score=Decimal("-0.08"), n_outcomes=50)
    draft = rule_propose_param_tune("dca", params, m)
    assert draft is None, "should return None when already at the maximum"


# ---------------------------------------------------------------------------
# OI-delta tests
# ---------------------------------------------------------------------------

def test_param_tune_oi_delta_raises_threshold_on_poor_winrate():
    """Poor win_rate → raise oi_threshold_pct (require bigger OI surge).

    oi_delta knobs (sorted): ['horizon_s', 'oi_threshold_pct', 'sl_pct', 'tp_pct'] → 4 knobs.
    n=52 → 52 % 4 = 0 → 'horizon_s'.
    """
    m = _metrics(strategy_id="oi_delta", win_rate=Decimal("0.20"), n_outcomes=52)
    draft = rule_propose_param_tune("oi_delta", _oi_delta_params(), m)
    assert draft is not None
    after_horizon = int(draft.after_params["horizon_s"])
    assert after_horizon > 300


def test_param_tune_oi_delta_threshold_knob():
    """oi_delta knobs: ['horizon_s', 'oi_threshold_pct', 'sl_pct', 'tp_pct'].
    n=53 → 53 % 4 = 1 → 'oi_threshold_pct'.
    """
    m = _metrics(strategy_id="oi_delta", win_rate=Decimal("0.20"), n_outcomes=53)
    draft = rule_propose_param_tune("oi_delta", _oi_delta_params(), m)
    assert draft is not None
    after_thr = Decimal(draft.after_params["oi_threshold_pct"])
    assert after_thr > Decimal("0.015")


# ---------------------------------------------------------------------------
# Guard-rail tests
# ---------------------------------------------------------------------------

def test_param_tune_unknown_strategy_returns_none():
    """A strategy_id not in PARAM_TUNERS should return None."""
    m = _metrics(strategy_id="matrix_agent", n_outcomes=50)
    draft = rule_propose_param_tune("matrix_agent", {"weights": {}}, m)
    assert draft is None, "matrix_agent is not in PARAM_TUNERS"


def test_param_tune_unknown_strategy_foo_returns_none():
    """Completely unknown strategy returns None."""
    m = _metrics(strategy_id="foo", n_outcomes=50)
    draft = rule_propose_param_tune("foo", {}, m)
    assert draft is None


def test_param_tune_high_score_no_proposal():
    """avg_score above trigger → no proposal even for a known strategy."""
    m = _metrics(strategy_id="grid", avg_score=Decimal("-0.03"), n_outcomes=50)
    draft = rule_propose_param_tune("grid", _grid_params(), m)
    assert draft is None, "score above NEG_AVG_SCORE_TRIGGER (-0.05); should not fire"


def test_param_tune_below_min_outcomes_no_proposal():
    """n_outcomes below MIN_N_OUTCOMES → no proposal."""
    m = _metrics(strategy_id="grid", n_outcomes=5)
    draft = rule_propose_param_tune("grid", _grid_params(), m)
    assert draft is None


def test_param_tune_missing_param_returns_none():
    """If the selected knob is not present in current_params → None."""
    # Empty params for grid — no price_band_pct or horizon_s
    m = _metrics(strategy_id="grid", n_outcomes=50)
    draft = rule_propose_param_tune("grid", {}, m)
    assert draft is None, "missing param should skip gracefully"


def test_param_tune_preserves_other_params():
    """After proposal, params not being tuned should be preserved.

    Use n=52 → idx=0 → horizon_s; supply all knob keys so selector finds them.
    """
    params = {
        "n_grids": 10,
        "price_band_pct": "0.02",
        "horizon_s": 300,
        "tp_pct": "0.010",
        "sl_pct": "0.015",
    }
    m = _metrics(strategy_id="grid", win_rate=Decimal("0.30"), n_outcomes=52)
    draft = rule_propose_param_tune("grid", params, m)
    assert draft is not None
    # All keys from before should still appear in after_params
    for k in params:
        assert k in draft.after_params, f"key {k!r} missing from after_params"


# ---------------------------------------------------------------------------
# Coverage assertions: new strategies are registered in PARAM_TUNERS
# ---------------------------------------------------------------------------

def test_new_strategies_in_param_tuners():
    """All four newly added strategies must appear in PARAM_TUNERS with at
    least one tunable knob each."""
    from reflection.mutate import PARAM_TUNERS
    new_strategies = [
        "funding_reversion",
        "bist_gap_fade",
        "bist_intraday_reversion",
        "bist_volume_breakout",
    ]
    for sid in new_strategies:
        assert sid in PARAM_TUNERS, f"{sid!r} missing from PARAM_TUNERS"
        assert PARAM_TUNERS[sid], f"PARAM_TUNERS[{sid!r}] is empty"


# ---------------------------------------------------------------------------
# TP/SL knob assertions
# ---------------------------------------------------------------------------

def test_tp_sl_knobs_present_for_all_non_delta_strategies():
    """Every non-delta-neutral strategy in PARAM_TUNERS must expose
    tp_pct and sl_pct knobs. cash_and_carry is explicitly excluded."""
    from reflection.mutate import PARAM_TUNERS

    strategies_with_tp_sl = [
        "funding_reversion",
        "grid",
        "oi_delta",
        "momentum_xs",
        "screener_follow",
        "bist_gap_fade",
        "bist_intraday_reversion",
        "bist_volume_breakout",
        "bist_news_event",
    ]
    for sid in strategies_with_tp_sl:
        assert sid in PARAM_TUNERS, f"{sid!r} missing from PARAM_TUNERS"
        assert "tp_pct" in PARAM_TUNERS[sid], f"tp_pct knob missing for {sid!r}"
        assert "sl_pct" in PARAM_TUNERS[sid], f"sl_pct knob missing for {sid!r}"


def test_cash_and_carry_has_no_tp_sl_knobs():
    """cash_and_carry is delta-neutral; tp_pct/sl_pct must NOT be in its tuner."""
    from reflection.mutate import PARAM_TUNERS
    assert "cash_and_carry" in PARAM_TUNERS
    assert "tp_pct" not in PARAM_TUNERS["cash_and_carry"], (
        "cash_and_carry must NOT have tp_pct in PARAM_TUNERS"
    )
    assert "sl_pct" not in PARAM_TUNERS["cash_and_carry"], (
        "cash_and_carry must NOT have sl_pct in PARAM_TUNERS"
    )


# ---------------------------------------------------------------------------
# Mutation spiral guards (2026-09-13)
# ---------------------------------------------------------------------------


def test_param_tune_oi_delta_below_30_returns_none():
    """oi_delta must not fire on n<30 — thin sample causes mutation spiral.

    Engineering lesson: 10-trade win_rate has ±16pp noise; n≥30 required.
    The spiral v3/v4/v5 all had n≤13; this gate blocks that path.
    """
    for n in (10, 12, 13, 15, 29):
        m = _metrics(
            strategy_id="oi_delta",
            n_outcomes=n,
            win_rate=Decimal("0.20"),
            total_pnl_usd=Decimal("-0.218"),
        )
        draft = rule_propose_param_tune("oi_delta", _oi_delta_params(), m)
        assert draft is None, (
            f"oi_delta param_tune must not fire at n={n} (below PARAM_TUNE_MIN_N=30)"
        )


def test_param_tune_oi_delta_fires_at_30():
    """oi_delta should produce a proposal when n==30 and losing."""
    m = _metrics(
        strategy_id="oi_delta",
        n_outcomes=30,
        win_rate=Decimal("0.20"),
        total_pnl_usd=Decimal("-0.218"),
    )
    draft = rule_propose_param_tune("oi_delta", _oi_delta_params(), m)
    assert draft is not None, "oi_delta should fire at n=30"
    assert draft.proposal_type == "param_tune"


def test_param_tune_skip_knobs_selects_different_knob():
    """skip_knobs forces the rotation to skip the nominated knob.

    oi_delta sorted knobs: ['horizon_s', 'oi_threshold_pct', 'sl_pct', 'tp_pct'].
    At n=32, without skip: 32 % 4 = 0 → 'horizon_s'.
    With skip_knobs={'horizon_s'}: available=['oi_threshold_pct','sl_pct','tp_pct'],
    32 % 3 = 2 → 'tp_pct'.
    """
    m = _metrics(
        strategy_id="oi_delta",
        n_outcomes=32,
        win_rate=Decimal("0.20"),
        total_pnl_usd=Decimal("-0.218"),
    )
    draft_no_skip = rule_propose_param_tune("oi_delta", _oi_delta_params(), m)
    assert draft_no_skip is not None
    # horizon_s is the default knob at n=32
    assert draft_no_skip.after_params.get("horizon_s") != _oi_delta_params().get("horizon_s"), (
        "expected horizon_s to be tuned at n=32 without skip"
    )

    draft_skip = rule_propose_param_tune(
        "oi_delta", _oi_delta_params(), m, skip_knobs=frozenset({"horizon_s"})
    )
    assert draft_skip is not None
    # horizon_s must be unchanged; a different knob must move
    assert draft_skip.after_params.get("horizon_s") == _oi_delta_params().get("horizon_s"), (
        "horizon_s should be skipped when in skip_knobs"
    )
    # Confirm some other knob changed
    changed = {k for k in _oi_delta_params() if draft_skip.after_params.get(k) != _oi_delta_params()[k]}
    assert changed, "at least one non-horizon_s knob should change when horizon_s is skipped"
    assert "horizon_s" not in changed, "horizon_s must not change when skip_knobs contains it"


def test_param_tune_skip_knobs_all_falls_back_to_full_rotation():
    """When every knob is in skip_knobs, fall back to the full sorted list."""
    all_knobs = frozenset({"horizon_s", "oi_threshold_pct", "sl_pct", "tp_pct"})
    m = _metrics(
        strategy_id="oi_delta",
        n_outcomes=32,
        win_rate=Decimal("0.20"),
        total_pnl_usd=Decimal("-0.218"),
    )
    draft = rule_propose_param_tune(
        "oi_delta", _oi_delta_params(), m, skip_knobs=all_knobs
    )
    assert draft is not None, "should still produce a proposal when all knobs are in skip_knobs"


def test_param_tune_grid_requires_30_outcomes():
    """grid is in PARAM_TUNE_MIN_N with min=30 — same noise lesson as oi_delta.

    Confirms that n<30 for grid also returns None.
    """
    m = _metrics(
        strategy_id="grid",
        n_outcomes=10,
        win_rate=Decimal("0.20"),
        total_pnl_usd=Decimal("-5.0"),
    )
    draft = rule_propose_param_tune("grid", _grid_params(), m)
    assert draft is None, "grid requires n>=30 (PARAM_TUNE_MIN_N)"


def test_tp_sl_knob_ranges_sane():
    """For every strategy with tp/sl knobs, step > 0, min < max, defaults > 0."""
    from reflection.mutate import PARAM_TUNERS

    for sid, tuner in PARAM_TUNERS.items():
        for knob in ("tp_pct", "sl_pct"):
            if knob not in tuner:
                continue
            step, lo, hi = tuner[knob]
            assert step > 0, f"{sid}.{knob}: step must be > 0"
            assert lo < hi, f"{sid}.{knob}: min must be < max"
            assert lo > 0, f"{sid}.{knob}: min must be > 0"


def test_tp_sl_knob_tune_executes():
    """Spot-check that tp_pct can actually be selected and mutated for grid."""
    from decimal import Decimal
    from reflection.mutate import PARAM_TUNERS, rule_propose_param_tune
    from reflection.metrics import StrategyMetrics

    n_knobs = len(PARAM_TUNERS["grid"])
    # Find n_outcomes that selects tp_pct
    param_names = sorted(PARAM_TUNERS["grid"].keys())
    tp_idx = param_names.index("tp_pct")

    # Construct n_outcomes s.t. idx = tp_idx
    n_outcomes = tp_idx + 10 * n_knobs  # ensure n >= MIN_N_OUTCOMES and selects tp_pct
    m = StrategyMetrics(
        strategy_id="grid",
        version=1,
        n_outcomes=n_outcomes,
        avg_score=Decimal("-0.08"),
        win_rate=Decimal("0.30"),
        total_pnl_usd=Decimal("-5.0"),
        by_symbol={},
    )
    params = {
        "n_grids": 10,
        "price_band_pct": "0.02",
        "horizon_s": 300,
        "tp_pct": "0.010",
        "sl_pct": "0.015",
    }
    draft = rule_propose_param_tune("grid", params, m)
    assert draft is not None
    assert "tp_pct" in draft.after_params or "sl_pct" in draft.after_params, (
        "TP/SL knob should be tunable for grid"
    )


def test_funding_reversion_param_tune():
    """funding_reversion: poor win_rate triggers a param proposal."""
    from reflection.mutate import PARAM_TUNERS
    params = {
        "high_funding": "0.0002",
        "funding_cap": "0.0005",
        "horizon_s": 600,
    }
    m = _metrics(strategy_id="funding_reversion", win_rate=Decimal("0.30"), n_outcomes=50)
    draft = rule_propose_param_tune("funding_reversion", params, m)
    assert draft is not None
    assert draft.proposal_type == "param_tune"
    # One of the knobs must change
    changed = any(
        str(draft.after_params.get(k)) != str(params[k])
        for k in PARAM_TUNERS["funding_reversion"]
        if k in params
    )
    assert changed, "No param changed for funding_reversion"


def test_bist_gap_fade_param_tune():
    """bist_gap_fade: poor win_rate triggers a param proposal."""
    from reflection.mutate import PARAM_TUNERS
    params = {
        "gap_threshold": "0.015",
        "gap_cap": "0.05",
        "horizon_s": 1800,
    }
    m = _metrics(strategy_id="bist_gap_fade", win_rate=Decimal("0.30"), n_outcomes=50)
    draft = rule_propose_param_tune("bist_gap_fade", params, m)
    assert draft is not None
    changed = any(
        str(draft.after_params.get(k)) != str(params[k])
        for k in PARAM_TUNERS["bist_gap_fade"]
        if k in params
    )
    assert changed, "No param changed for bist_gap_fade"


def test_bist_intraday_reversion_param_tune():
    """bist_intraday_reversion: poor win_rate triggers a param proposal."""
    from reflection.mutate import PARAM_TUNERS
    params = {
        "drop_threshold": "0.03",
        "drop_cap": "0.07",
        "horizon_s": 3600,
    }
    m = _metrics(strategy_id="bist_intraday_reversion", win_rate=Decimal("0.30"), n_outcomes=50)
    draft = rule_propose_param_tune("bist_intraday_reversion", params, m)
    assert draft is not None
    changed = any(
        str(draft.after_params.get(k)) != str(params[k])
        for k in PARAM_TUNERS["bist_intraday_reversion"]
        if k in params
    )
    assert changed, "No param changed for bist_intraday_reversion"


def test_bist_volume_breakout_param_tune():
    """bist_volume_breakout: poor win_rate triggers a param proposal."""
    from reflection.mutate import PARAM_TUNERS
    params = {
        "vol_mult": "3.0",
        "vol_mult_cap": "8.0",
        "horizon_s": 900,
    }
    m = _metrics(strategy_id="bist_volume_breakout", win_rate=Decimal("0.30"), n_outcomes=50)
    draft = rule_propose_param_tune("bist_volume_breakout", params, m)
    assert draft is not None
    changed = any(
        str(draft.after_params.get(k)) != str(params[k])
        for k in PARAM_TUNERS["bist_volume_breakout"]
        if k in params
    )
    assert changed, "No param changed for bist_volume_breakout"

"""Per-symbol cost: a wide book must cost more than a tight one, and a missing
measurement must never make a symbol look free."""

from __future__ import annotations

import json

from matrix_shared import symbol_costs as SC


def test_crossing_costs_half_the_spread():
    assert SC.slippage_from_spread(4.0) == 2.0
    assert SC.slippage_from_spread(1.16) == 0.58        # UNIUSDT, measured
    assert SC.slippage_from_spread(5.95) == 2.975       # BRUSDT, measured


def test_floored_and_capped_so_a_freak_book_cannot_distort_the_gate():
    assert SC.slippage_from_spread(0.0) == SC.MIN_SLIPPAGE_BPS
    assert SC.slippage_from_spread(-3) == SC.MIN_SLIPPAGE_BPS
    assert SC.slippage_from_spread(10_000) == SC.MAX_SLIPPAGE_BPS


def test_unmeasured_symbol_returns_none_so_the_caller_keeps_the_flat_cost(tmp_path, monkeypatch):
    p = tmp_path / "symbol_costs.json"
    p.write_text(json.dumps({"slippage_bps": {"BRUSDT": 2.98}}))
    monkeypatch.setattr(SC, "COSTS_PATH", p)
    SC.clear_cache()
    assert SC.slippage_bps_for("BRUSDT") == 2.98
    assert SC.slippage_bps_for("NEVERSEENUSDT") is None
    assert SC.slippage_bps_for(None) is None


def test_a_corrupt_or_missing_file_degrades_to_the_flat_cost(tmp_path, monkeypatch):
    missing = tmp_path / "nope.json"
    monkeypatch.setattr(SC, "COSTS_PATH", missing)
    SC.clear_cache()
    assert SC.slippage_bps_for("BRUSDT") is None

    corrupt = tmp_path / "bad.json"
    corrupt.write_text("{not json")
    monkeypatch.setattr(SC, "COSTS_PATH", corrupt)
    SC.clear_cache()
    assert SC.slippage_bps_for("BRUSDT") is None

"""strategy.params — DB params must reach the constructor, version must stamp.

Pure unit tests (no DB): regressions here mean reflection/labs mutations are
silently ignored again (docs/AUTONOMY_PLAN.md §5 bug #1/#2).
"""

from __future__ import annotations

from decimal import Decimal

from strategy.modules.bist.gap_fade import BistGapFade
from strategy.modules.crypto.dca import Dca
from strategy.modules.crypto.funding_reversion import FundingReversion
from strategy.modules.crypto.grid import Grid
from strategy.modules.crypto.oi_breakout import OiBreakout
from strategy.modules.crypto.oi_delta import OiDelta
from strategy.modules import STRATEGIES_BY_MARKET
from strategy.params import build_kwargs, instantiate


def test_build_kwargs_coerces_json_types():
    kw = build_kwargs(
        FundingReversion,
        {"high_funding": "0.0003", "funding_cap": 0.0008, "horizon_s": "1200",
         "tp_pct": "0.01", "sl_pct": None},
    )
    assert kw["high_funding"] == Decimal("0.0003")
    assert kw["funding_cap"] == Decimal("0.0008")
    assert kw["horizon_s"] == 1200 and isinstance(kw["horizon_s"], int)
    assert kw["tp_pct"] == Decimal("0.01")
    assert kw["sl_pct"] is None


def test_build_kwargs_aliases_and_drops_unknown():
    kw = build_kwargs(
        Grid,
        {"price_band_pct": "0.03", "n_grids": 12, "horizon_s": 600,
         "weights": {"a": 1}, "symbols": ["X"], "bogus": 1},
    )
    assert kw == {"band_pct": Decimal("0.03"), "n_grids": 12, "horizon_s": 600}


def test_instantiate_stamps_version_and_applies_params():
    strat = instantiate(
        FundingReversion,
        symbols=["BTCUSDT"],
        version=7,
        params={"high_funding": "0.0005", "horizon_s": 900},
    )
    assert strat.version == 7
    assert strat.symbols == ["BTCUSDT"]
    assert strat.high_funding == Decimal("0.0005")
    assert strat.horizon_seconds == 900
    # class-level default untouched (module default is v6, the 2026-09-15
    # geometry rework; the DB config above overrides it per instance)
    assert FundingReversion.version == 6


def test_instantiate_without_config_keeps_defaults():
    strat = instantiate(Dca, symbols=["ETHUSDT"])
    assert strat.version == 1
    assert strat.symbols == ["ETHUSDT"]


def test_grid_oi_strategies_accept_tuned_horizon_and_threshold():
    g = instantiate(Grid, symbols=["BTCUSDT"], params={"horizon_s": 900})
    assert g.horizon_seconds == 900
    d = instantiate(OiDelta, symbols=["BTCUSDT"],
                    params={"horizon_s": 600, "oi_threshold_pct": "0.02"})
    assert d.horizon_seconds == 600 and d.oi_threshold_pct == Decimal("0.02")
    b = instantiate(OiBreakout, symbols=["BTCUSDT"],
                    params={"horizon_s": 1200, "oi_threshold_pct": "0.03",
                            "tp_pct": "0.02", "sl_pct": "0.01"})
    assert b.horizon_seconds == 1200 and b.oi_threshold_pct == Decimal("0.03")
    assert b.tp_pct == Decimal("0.02") and b.sl_pct == Decimal("0.01")


def test_bist_strategy_without_symbols_kwarg():
    strat = instantiate(BistGapFade, symbols=None, version=3,
                        params={"gap_threshold": "0.02", "horizon_s": 1200})
    assert strat.version == 3
    assert strat.gap_threshold == Decimal("0.02")
    assert strat.horizon_seconds == 1200


def test_every_registered_strategy_instantiates_with_empty_params():
    for market, classes in STRATEGIES_BY_MARKET.items():
        for cls in classes:
            strat = instantiate(cls, symbols=["BTCUSDT"] if market == "crypto" else None,
                                version=2, params={})
            assert strat.version == 2, cls


def test_challenger_instances_are_tagged_and_versioned():
    from matrix_shared.markets import get_market
    from strategy.main import _instantiate_for_market

    market = get_market("crypto")
    configs = {("grid", "crypto"): (4, {"n_grids": 8})}
    shadows = {("grid", "crypto"): (5, {"n_grids": 12})}
    instances = _instantiate_for_market(market, ["BTCUSDT"], configs, shadows)
    grids = [i for i in instances if i.id == "grid"]
    assert [(g.version, g.n_grids, getattr(g, "matrix_is_shadow", False)) for g in grids] == [
        (4, 8, False), (5, 12, True),
    ]
    # No shadow → champion only.
    only = [i for i in _instantiate_for_market(market, ["BTCUSDT"], configs, {}) if i.id == "grid"]
    assert len(only) == 1 and not getattr(only[0], "matrix_is_shadow", False)


def test_shadow_only_strategy_runs_as_challenger():
    """A new strategy registered only as `shadow` emits on the shadow wallet;
    a strategy with neither row stays off."""
    from matrix_shared.markets import get_market
    from strategy.main import _instantiate_for_market

    market = get_market("crypto")
    configs = {("dca", "crypto"): (1, {})}
    shadows = {("grid", "crypto"): (2, {"n_grids": 12})}
    instances = _instantiate_for_market(market, ["BTCUSDT"], configs, shadows)
    grids = [i for i in instances if i.id == "grid"]
    assert [(g.version, getattr(g, "matrix_is_shadow", False)) for g in grids] == [(2, True)]
    assert not [i for i in instances if i.id == "oi_delta"]

"""DB-free smoke tests for the per-market strategy registry.

Asserts that:
- Every registered MarketAdapter has at least one strategy class.
- Every strategy class declares a `market` attribute matching the adapter.
- Strategy IDs are unique across all markets (collision = silent override).
"""

from __future__ import annotations

from matrix_shared.markets import all_markets

from strategy.modules import STRATEGIES_BY_MARKET


def test_every_market_has_strategies() -> None:
    market_names = {m.name for m in all_markets()}
    assert market_names == set(STRATEGIES_BY_MARKET), (
        f"market <-> strategy registry mismatch: "
        f"markets={market_names}, registry={set(STRATEGIES_BY_MARKET)}"
    )
    for name, strategies in STRATEGIES_BY_MARKET.items():
        assert strategies, f"market {name!r} registered with zero strategies"


def test_every_strategy_declares_matching_market() -> None:
    for market_name, classes in STRATEGIES_BY_MARKET.items():
        for cls in classes:
            assert hasattr(cls, "market"), f"{cls.__name__} missing `market` attr"
            assert cls.market == market_name, (
                f"{cls.__name__} declares market={cls.market!r} but registered "
                f"under {market_name!r}"
            )


def test_strategy_ids_unique_across_markets() -> None:
    seen: dict[str, str] = {}
    for market_name, classes in STRATEGIES_BY_MARKET.items():
        for cls in classes:
            sid = cls.id
            assert sid not in seen, (
                f"duplicate strategy id {sid!r}: {seen[sid]} vs {market_name}"
            )
            seen[sid] = market_name


def test_no_retired_strategies_present() -> None:
    """trade_flow_imbalance was retired 2026-05-26 (-$229/24h, 12.8% win)."""
    retired = {"trade_flow_imbalance", "matrix_agent"}
    for classes in STRATEGIES_BY_MARKET.values():
        for cls in classes:
            assert cls.id not in retired, f"retired strategy still wired: {cls.id}"

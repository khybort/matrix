"""Global registry of `MarketAdapter` instances.

`register(adapter)` is called by each concrete module on import. Consumers
read via `get_market`, `all_markets`, `infer_market`.
"""

from __future__ import annotations

from .base import MarketAdapter

_MARKETS: dict[str, MarketAdapter] = {}


def register(market: MarketAdapter) -> None:
    if market.name in _MARKETS:
        existing = _MARKETS[market.name]
        if type(existing) is type(market):
            return  # idempotent re-import (e.g. uvicorn reloader)
        raise ValueError(
            f"market name collision: {market.name!r} already registered as "
            f"{type(existing).__name__}"
        )
    _MARKETS[market.name] = market


def get_market(name: str) -> MarketAdapter:
    if name not in _MARKETS:
        raise KeyError(
            f"unknown market {name!r}; registered: {sorted(_MARKETS)}"
        )
    return _MARKETS[name]


def all_markets() -> list[MarketAdapter]:
    return list(_MARKETS.values())


def infer_market(symbol: str) -> MarketAdapter:
    """Return the market that claims `symbol`.

    Raises `ValueError` if no market claims it, or if more than one does
    (ambiguous routing — a strict failure mode by design).
    """
    matches = [m for m in _MARKETS.values() if m.claims_symbol(symbol)]
    if not matches:
        raise ValueError(f"no market claims symbol: {symbol!r}")
    if len(matches) > 1:
        raise ValueError(
            f"ambiguous symbol {symbol!r}: claimed by "
            f"{sorted(m.name for m in matches)}"
        )
    return matches[0]

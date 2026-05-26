"""Strategy module registry, organised by market.

`STRATEGIES_BY_MARKET[market_name]` returns the list of strategy classes
registered for that market. The dispatcher in `strategy.main` walks this
dict against `matrix_shared.markets.all_markets()`.

Adding a new market = create `modules/<market>/__init__.py` exporting
`STRATEGIES` and add it to the dict below.
"""

from __future__ import annotations

from strategy.modules.bist import STRATEGIES as _BIST_STRATEGIES
from strategy.modules.crypto import STRATEGIES as _CRYPTO_STRATEGIES

STRATEGIES_BY_MARKET: dict[str, list[type]] = {
    "crypto": _CRYPTO_STRATEGIES,
    "bist": _BIST_STRATEGIES,
}

__all__ = ["STRATEGIES_BY_MARKET"]

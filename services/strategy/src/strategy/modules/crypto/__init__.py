"""Crypto-market strategy modules.

Add the class to `STRATEGIES` to register it with the dispatcher.
"""

from __future__ import annotations

from strategy.modules.crypto.dca import Dca
from strategy.modules.crypto.funding_reversion import FundingReversion
from strategy.modules.crypto.grid import Grid
from strategy.modules.crypto.oi_breakout import OiBreakout
from strategy.modules.crypto.oi_delta import OiDelta

STRATEGIES: list[type] = [
    FundingReversion,
    OiDelta,
    OiBreakout,
    Grid,
    Dca,
]

__all__ = [
    "STRATEGIES",
    "Dca",
    "FundingReversion",
    "Grid",
    "OiBreakout",
    "OiDelta",
]

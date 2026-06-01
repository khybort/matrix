"""Crypto-market strategy modules.

Add the class to `STRATEGIES` to register it with the dispatcher.
Remove to disable — module files are retained so the strategy can be
re-registered once its signal recovers (see ROADMAP.md §lab-comeback).
"""

from __future__ import annotations

from strategy.modules.crypto.cash_and_carry import CashAndCarry
from strategy.modules.crypto.dca import Dca  # noqa: F401 (retained, not active)
from strategy.modules.crypto.funding_reversion import FundingReversion
from strategy.modules.crypto.grid import Grid
from strategy.modules.crypto.momentum_xs import MomentumXs
from strategy.modules.crypto.oi_breakout import OiBreakout  # noqa: F401 (retained, not active)
from strategy.modules.crypto.oi_delta import OiDelta
from strategy.modules.crypto.screener_follow import ScreenerFollow

STRATEGIES: list[type] = [
    FundingReversion,
    OiDelta,
    OiBreakout,
    Grid,
    MomentumXs,
    ScreenerFollow,
    CashAndCarry,
    Dca,
]

__all__ = [
    "STRATEGIES",
    "CashAndCarry",
    "Dca",
    "FundingReversion",
    "Grid",
    "MomentumXs",
    "OiBreakout",
    "OiDelta",
    "ScreenerFollow",
]

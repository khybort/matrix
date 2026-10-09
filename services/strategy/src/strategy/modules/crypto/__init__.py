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
from strategy.modules.crypto.inverse_carry import InverseCarry
from strategy.modules.crypto.iv_inversion import IvInversion
from strategy.modules.crypto.momentum_xs import MomentumXs
from strategy.modules.crypto.neg_funding_carry import NegFundingCarry
from strategy.modules.crypto.oi_breakout import OiBreakout  # noqa: F401 (retained, not active)
from strategy.modules.crypto.oi_delta import OiDelta
from strategy.modules.crypto.screener_follow import ScreenerFollow
from strategy.modules.crypto.xexch_funding_arb import XexchFundingArb

STRATEGIES: list[type] = [
    FundingReversion,
    OiDelta,
    OiBreakout,
    Grid,
    MomentumXs,
    ScreenerFollow,
    CashAndCarry,
    NegFundingCarry,
    InverseCarry,
    IvInversion,
    XexchFundingArb,
    Dca,
]

__all__ = [
    "STRATEGIES",
    "CashAndCarry",
    "Dca",
    "FundingReversion",
    "Grid",
    "InverseCarry",
    "IvInversion",
    "MomentumXs",
    "NegFundingCarry",
    "OiBreakout",
    "OiDelta",
    "ScreenerFollow",
    "XexchFundingArb",
]

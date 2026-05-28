"""Crypto-market strategy modules.

Add the class to `STRATEGIES` to register it with the dispatcher.
Remove to disable — module files are retained so the strategy can be
re-registered once its signal recovers (see ROADMAP.md §lab-comeback).
"""

from __future__ import annotations

from strategy.modules.crypto.dca import Dca  # noqa: F401 (retained, not active)
from strategy.modules.crypto.funding_reversion import FundingReversion
from strategy.modules.crypto.grid import Grid
from strategy.modules.crypto.oi_breakout import OiBreakout  # noqa: F401 (retained, not active)
from strategy.modules.crypto.oi_delta import OiDelta

STRATEGIES: list[type] = [
    FundingReversion,
    OiDelta,
    Grid,
    # Dca: -$8.67/24h, avg_score -0.148 (2026-05-28). Long-only accumulator hurts
    #      in sideways/down markets; re-enable when funding_rate trend confirms
    #      sustained upward pressure or lab genome proves its signal.
    # OiBreakout: -$1.81/24h, 7% win rate (2026-05-28). Signal not yet validated;
    #             kept in lab evolution pool, re-register once fitness >= 0.05.
]

__all__ = [
    "STRATEGIES",
    "Dca",
    "FundingReversion",
    "Grid",
    "OiBreakout",
    "OiDelta",
]

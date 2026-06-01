"""Shared trading micro-utilities used by paper-trade, labs, and backtest.

Kept dependency-free so any service can import slippage math without
pulling in the backtest package.
"""

from __future__ import annotations

from decimal import Decimal

# Matches services/backtest/paper_trade.py — keep in sync.
SLIPPAGE_BPS = Decimal("2")


def apply_slippage(price: Decimal, side: str, *, opening: bool) -> Decimal:
    """Apply symmetric slippage on entry or exit.

    Long entries pay up; long exits sell down. Shorts mirror.
    """
    bps = SLIPPAGE_BPS / Decimal("10000")
    if opening:
        return price * (Decimal("1") + bps) if side == "long" else price * (Decimal("1") - bps)
    return price * (Decimal("1") - bps) if side == "long" else price * (Decimal("1") + bps)

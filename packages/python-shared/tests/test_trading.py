"""Slippage helper tests — must match paper_trade semantics."""

from __future__ import annotations

from decimal import Decimal

from matrix_shared.trading import SLIPPAGE_BPS, apply_slippage


def test_long_entry_pays_up():
    px = Decimal("100")
    adj = apply_slippage(px, "long", opening=True)
    assert adj > px
    assert adj == px * (Decimal("1") + SLIPPAGE_BPS / Decimal("10000"))


def test_long_exit_sells_down():
    px = Decimal("100")
    adj = apply_slippage(px, "long", opening=False)
    assert adj < px


def test_round_trip_long_is_negative_on_flat_price():
    entry = apply_slippage(Decimal("100"), "long", opening=True)
    exit_ = apply_slippage(Decimal("100"), "long", opening=False)
    pnl_pct = (exit_ - entry) / entry
    assert pnl_pct < 0

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


# --- market-aware cost model ---------------------------------------------

def test_execution_cost_uses_market_fee_model():
    from matrix_shared.trading import execution_cost_bps, round_trip_cost_pct

    assert execution_cost_bps(None) == SLIPPAGE_BPS
    crypto = execution_cost_bps("crypto", "BTCUSDT")
    assert crypto == Decimal("7.5")  # 5.5 taker + 2 slippage
    assert round_trip_cost_pct("crypto") == Decimal("15") / Decimal("10000")
    assert execution_cost_bps("bist", "THYAO") == Decimal("20")  # 15 + 5
    assert execution_cost_bps("nope") == SLIPPAGE_BPS  # unknown → legacy


def test_apply_slippage_market_aware_is_costlier_than_legacy():
    px = Decimal("100")
    legacy = apply_slippage(px, "long", opening=True)
    crypto = apply_slippage(px, "long", opening=True, asset_class="crypto", symbol="BTCUSDT")
    assert crypto > legacy > px
    assert crypto == px * (Decimal("1") + Decimal("7.5") / Decimal("10000"))
    # shorts mirror; delta_neutral untouched
    assert apply_slippage(px, "short", opening=True, asset_class="crypto") < px
    assert apply_slippage(px, "delta_neutral", opening=True, asset_class="crypto") == px


def test_funding_pnl_sign_and_magnitude():
    from matrix_shared.trading import funding_pnl_usd

    # 8h hold at +0.01% funding on $1000: long pays $0.10, short receives it.
    assert funding_pnl_usd("long", Decimal("1000"), Decimal("0.0001"), Decimal("8")) == Decimal("-0.1")
    assert funding_pnl_usd("short", Decimal("1000"), Decimal("0.0001"), Decimal("8")) == Decimal("0.1")
    assert funding_pnl_usd("long", Decimal("1000"), None, Decimal("8")) == 0
    assert funding_pnl_usd("delta_neutral", Decimal("1000"), Decimal("0.0001"), Decimal("8")) == 0

"""Shared trading micro-utilities used by paper-trade, labs, and backtest.

Execution cost model (docs/AUTONOMY_PLAN.md P0.6): every fill pays the
market's taker fee plus a slippage allowance, both from
`MarketAdapter.fees(symbol)` (`FeeModel.taker_bps + slippage_bps`). Before
2026-09-12 the paper engine charged a flat 2 bps and no fees at all, so the
learning loop optimised against a world ~11 bps/round-trip cheaper than
Bybit reality (5.5 bps taker each side).

`apply_slippage(..., asset_class=None)` keeps the legacy flat model for
callers that have no market context (labs genome scoring, unit tests).
"""

from __future__ import annotations

from decimal import Decimal

# Legacy flat allowance, used only when no asset_class is given.
SLIPPAGE_BPS = Decimal("2")
_BPS = Decimal("10000")


def execution_cost_bps(asset_class: str | None, symbol: str | None = None) -> Decimal:
    """Per-side cost in bps: taker fee + slippage for the market, else legacy 2."""
    if not asset_class:
        return SLIPPAGE_BPS
    try:
        from matrix_shared.markets import get_market  # lazy: registry imports models

        fees = get_market(asset_class).fees(symbol or "")
    except Exception:  # noqa: BLE001 — unknown market → legacy allowance, never crash a tick
        return SLIPPAGE_BPS
    # Prefer the slippage we have actually measured for this symbol over the
    # market's flat allowance (matrix_shared.symbol_costs): median spreads in
    # this universe range 1.2–6.0 bps, so one constant misprices both ends.
    slip = Decimal(fees.slippage_bps)
    try:
        from matrix_shared.symbol_costs import slippage_bps_for

        measured = slippage_bps_for(symbol)
        if measured is not None:
            slip = Decimal(str(measured))
    except Exception:  # noqa: BLE001 — measurement is an optional refinement
        pass
    return Decimal(fees.taker_bps) + slip


def round_trip_cost_pct(asset_class: str | None, symbol: str | None = None) -> Decimal:
    """Fraction of notional lost to a flat open+close (2 × per-side cost)."""
    return execution_cost_bps(asset_class, symbol) * 2 / _BPS


def apply_slippage(
    price: Decimal,
    side: str,
    *,
    opening: bool,
    asset_class: str | None = None,
    symbol: str | None = None,
) -> Decimal:
    """Adverse fill price for one side of a trade.

    Long entries pay up; long exits sell down. Shorts mirror. `delta_neutral`
    (two-leg carry) is priced by funding accrual elsewhere — no adjustment.
    """
    if side == "delta_neutral":
        return price
    bps = execution_cost_bps(asset_class, symbol) / _BPS
    if opening:
        return price * (Decimal("1") + bps) if side == "long" else price * (Decimal("1") - bps)
    return price * (Decimal("1") - bps) if side == "long" else price * (Decimal("1") + bps)


def funding_pnl_usd(
    side: str, notional_usd: Decimal, funding_rate_8h: Decimal | None, elapsed_hours: Decimal
) -> Decimal:
    """Funding paid/received over a perp hold. Longs pay a positive rate,
    shorts receive it; `delta_neutral` is handled by its own accrual model."""
    if funding_rate_8h is None or side not in ("long", "short") or elapsed_hours <= 0:
        return Decimal("0")
    accrued = notional_usd * (elapsed_hours / Decimal("8")) * Decimal(funding_rate_8h)
    return -accrued if side == "long" else accrued


def virtual_pnl_pct(
    *, entry_ref: Decimal, exit_mark: Decimal, side: str, asset_class: str | None, symbol: str | None
) -> Decimal | None:
    """Counterfactual PnL% for a prediction that was never traded, priced as if
    it had been filled at `entry_ref` and closed at `exit_mark` with the same
    market costs as a real paper trade (horizon exit only, no TP/SL)."""
    if side not in ("long", "short") or entry_ref <= 0 or exit_mark <= 0:
        return None
    entry = apply_slippage(entry_ref, side, opening=True, asset_class=asset_class, symbol=symbol)
    exit_ = apply_slippage(exit_mark, side, opening=False, asset_class=asset_class, symbol=symbol)
    if side == "long":
        return (exit_ - entry) / entry
    return (entry - exit_) / entry

"""Mirror paper-trade opens/closes to Bybit when LIVE is enabled (shadow mode).

Paper DB remains source of truth. Exchange failures are logged only — they
never roll back a paper position.
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any
from uuid import UUID

from loguru import logger

from matrix_shared.bybit_v5 import BybitOrder, BybitV5Client
from matrix_shared.db import shared_session_scope
from matrix_shared.models import PaperPosition, Prediction, Wallet
from matrix_shared.trading_safety import has_valid_certificate

_SHADOW_CTX_KEY = "bybit_shadow"
_QTY_STEP = Decimal("0.001")


def shadow_enabled() -> bool:
    live = os.environ.get("LIVE_EXECUTION_ENABLED", "false").strip().lower() == "true"
    shadow = os.environ.get("MATRIX_EXCHANGE_SHADOW", "true").strip().lower() != "false"
    return live and shadow


def _testnet() -> bool:
    return os.environ.get("BYBIT_TESTNET", "true").strip().lower() != "false"


def _shadow_symbol_allowed(symbol: str) -> bool:
    raw = os.environ.get(
        "MATRIX_SHADOW_SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT"
    ).strip()
    if raw in ("", "*"):
        return True
    allowed = {s.strip() for s in raw.split(",") if s.strip()}
    return symbol in allowed


def _qty_from_notional(notional: Decimal, price: Decimal) -> Decimal:
    if price <= 0:
        return _QTY_STEP
    raw = notional / price
    qty = (raw // _QTY_STEP) * _QTY_STEP
    return max(_QTY_STEP, qty)


def _bybit_side(paper_side: str, *, opening: bool) -> str | None:
    if paper_side == "long":
        return "Buy" if opening else "Sell"
    if paper_side == "short":
        return "Sell" if opening else "Buy"
    return None


async def _per_trade_allowed(
    *,
    wallet_id: UUID,
    strategy_id: str,
    asset_class: str,
    strategy_version: int,
    notional_usd: Decimal,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if os.environ.get("LIVE_EXECUTION_ENABLED", "false").strip().lower() != "true":
        reasons.append("LIVE_EXECUTION_ENABLED is not 'true'")
    if not await has_valid_certificate(strategy_id, asset_class, strategy_version):
        reasons.append(
            f"no valid paper_trade_certificate for "
            f"{strategy_id}/{asset_class}/v{strategy_version}"
        )
    async with shared_session_scope() as session:
        wallet = await session.get(Wallet, wallet_id)
        if wallet is None:
            reasons.append(f"wallet {wallet_id} not found")
            return False, reasons
        if wallet.circuit_tripped_at is not None:
            reasons.append("wallet daily-loss circuit is tripped")
        equity = Decimal(wallet.cash_usd) + Decimal(wallet.locked_usd)
        max_notional = equity * Decimal(wallet.max_position_pct)
        if notional_usd > max_notional:
            reasons.append(
                f"notional_usd={notional_usd} > max_position_pct*equity={max_notional}"
            )
    return len(reasons) == 0, reasons


async def _store_shadow_meta(prediction_id: UUID, patch: dict[str, Any]) -> None:
    async with shared_session_scope() as session:
        pred = await session.get(Prediction, prediction_id)
        if pred is None:
            return
        ctx = dict(pred.context or {})
        shadow = dict(ctx.get(_SHADOW_CTX_KEY) or {})
        shadow.update(patch)
        ctx[_SHADOW_CTX_KEY] = shadow
        pred.context = ctx


async def shadow_open_position(
    *,
    prediction: Prediction,
    wallet_id: UUID,
    entry_price: Decimal,
    notional_usd: Decimal,
) -> None:
    if not shadow_enabled():
        return
    if prediction.asset_class != "crypto" or prediction.exchange != "bybit":
        return
    if not _shadow_symbol_allowed(prediction.symbol):
        logger.debug("shadow skip open {}: not in allowlist", prediction.symbol)
        return
    side = _bybit_side(prediction.side, opening=True)
    if side is None:
        return

    allowed, reasons = await _per_trade_allowed(
        wallet_id=wallet_id,
        strategy_id=prediction.strategy_id,
        asset_class=prediction.asset_class,
        strategy_version=prediction.strategy_version,
        notional_usd=notional_usd,
    )
    if not allowed:
        logger.warning(
            "shadow skip open {} {}: {}",
            prediction.symbol,
            prediction.id,
            reasons,
        )
        return

    qty = _qty_from_notional(notional_usd, entry_price)
    client = BybitV5Client(testnet=_testnet())
    try:
        result = await client.place_order(
            BybitOrder(
                category="linear",
                symbol=prediction.symbol,
                side=side,
                order_type="Market",
                qty=qty,
            )
        )
    finally:
        await client.aclose()

    if result.dry_run or not result.ok:
        logger.warning(
            "shadow open failed {} {} dry_run={} raw={}",
            prediction.symbol,
            prediction.id,
            result.dry_run,
            result.raw,
        )
        return

    await _store_shadow_meta(
        prediction.id,
        {
            "open_order_id": result.order_id,
            "qty": str(qty),
            "network": "testnet" if _testnet() else "mainnet",
        },
    )
    logger.info(
        "shadow open ok {} orderId={} qty={} pred={}",
        prediction.symbol,
        result.order_id,
        qty,
        prediction.id,
    )


async def shadow_close_position(
    *,
    prediction: Prediction,
    position: PaperPosition,
) -> None:
    if not shadow_enabled():
        return
    if position.asset_class != "crypto" or position.exchange != "bybit":
        return
    if not _shadow_symbol_allowed(position.symbol):
        return
    side = _bybit_side(position.side, opening=False)
    if side is None:
        return

    async with shared_session_scope() as session:
        pred = await session.get(Prediction, prediction.id)
    if pred is None:
        return
    shadow = (pred.context or {}).get(_SHADOW_CTX_KEY) or {}
    qty_raw = shadow.get("qty")
    if not qty_raw:
        logger.debug("shadow skip close {}: no shadow qty in context", position.symbol)
        return
    qty = Decimal(str(qty_raw))

    allowed, reasons = await _per_trade_allowed(
        wallet_id=position.wallet_id,
        strategy_id=pred.strategy_id,
        asset_class=pred.asset_class,
        strategy_version=pred.strategy_version,
        notional_usd=position.notional_usd,
    )
    if not allowed:
        logger.warning(
            "shadow skip close {} {}: {}",
            position.symbol,
            position.id,
            reasons,
        )
        return

    client = BybitV5Client(testnet=_testnet())
    try:
        result = await client.place_order(
            BybitOrder(
                category="linear",
                symbol=position.symbol,
                side=side,
                order_type="Market",
                qty=qty,
                reduce_only=True,
            )
        )
    finally:
        await client.aclose()

    if result.dry_run or not result.ok:
        logger.warning(
            "shadow close failed {} {} dry_run={} raw={}",
            position.symbol,
            position.id,
            result.dry_run,
            result.raw,
        )
        return

    await _store_shadow_meta(
        pred.id,
        {"close_order_id": result.order_id},
    )
    logger.info(
        "shadow close ok {} orderId={} qty={} pos={}",
        position.symbol,
        result.order_id,
        qty,
        position.id,
    )

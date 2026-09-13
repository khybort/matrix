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
    closing: bool = False,
) -> tuple[bool, list[str]]:
    """The full docs/TRADING.md gate (matrix_shared.live_gate) — identical to
    services/execution. Closes evaluate posture/flag/cert/circuit only."""
    from matrix_shared.live_gate import should_submit_live

    decision = await should_submit_live(
        strategy_id=strategy_id,
        asset_class=asset_class,
        strategy_version=strategy_version,
        intended_notional_usd=notional_usd,
        wallet_id=wallet_id,
        closing=closing,
    )
    return decision.allowed, decision.reasons


_clients: dict[bool, BybitV5Client] = {}


def _client() -> BybitV5Client:
    """One client per network for the process so its TokenBucket actually
    limits across orders (a per-call client reset the bucket every time)."""
    key = _testnet()
    if key not in _clients:
        _clients[key] = BybitV5Client(testnet=key)
    return _clients[key]


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
    if (prediction.context or {}).get("is_shadow"):
        return  # challenger (shadow-wallet) trade — paper only, never mirrored
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
    result = await _client().place_order(
        BybitOrder(
            category="linear",
            symbol=prediction.symbol,
            side=side,
            order_type="Market",
            qty=qty,
        )
    )

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
    if pred is None or (pred.context or {}).get("is_shadow"):
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
        closing=True,
    )
    if not allowed:
        logger.warning(
            "shadow skip close {} {}: {}",
            position.symbol,
            position.id,
            reasons,
        )
        return

    result = await _client().place_order(
        BybitOrder(
            category="linear",
            symbol=position.symbol,
            side=side,
            order_type="Market",
            qty=qty,
            reduce_only=True,
        )
    )

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

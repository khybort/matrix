"""The ONE composite live-order gate (docs/TRADING.md hard limits).

Lives in matrix_shared so every code path that can reach an exchange —
`services/execution` and the paper engine's `exchange_shadow` mirror — runs
the identical checks. Before 2026-09-13 exchange_shadow had its own weaker
copy that skipped LIVE_CAPITAL_CAP_USD, the concurrent-position cap and the
per-strategy slot cap (docs/AUTONOMY_PLAN.md P0.7).

Layers, in order of cheapness to evaluate:
    0. Deployment posture (mainnet + MATRIX_CERT_* overrides → refuse)
    1. LIVE_EXECUTION_ENABLED env flag (default false; manual edit only)
    2. paper_trade_certificate gate
    3. Wallet circuit_tripped_at (daily-loss circuit breaker)
    4. Per-trade position cap (notional <= equity * max_position_pct)
    5. LIVE_CAPITAL_CAP_USD (env ceiling on exposed capital)
    6. max_concurrent_positions cap
    7. Per-strategy slot cap (strategy_slot_configs)

`closing=True` (reduce-only exits) evaluates 0-3 only: a position we already
hold must always be closable — capping an exit would strand exposure.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select

from matrix_shared.db import shared_session_scope
from matrix_shared.models import PaperPosition, Prediction, Wallet
from matrix_shared.models.slot_config import StrategySlotConfig
from matrix_shared.trading_safety import (
    LIVE_EXECUTION_REQUIRES_CERT,
    has_valid_certificate,
    mainnet_refusal_reasons,
)


@dataclass(slots=True)
class GateDecision:
    allowed: bool
    reasons: list[str]
    snapshot: dict[str, object]


def env_live_enabled() -> bool:
    """LIVE_EXECUTION_ENABLED=true ONLY from an operator-edited .env."""
    return os.environ.get("LIVE_EXECUTION_ENABLED", "false").strip().lower() == "true"


def env_capital_cap_usd() -> Decimal:
    raw = os.environ.get("LIVE_CAPITAL_CAP_USD", "2000")
    try:
        return Decimal(str(raw))
    except (ValueError, ArithmeticError):
        return Decimal("0")  # bogus cap → most restrictive


async def should_submit_live(
    *,
    strategy_id: str,
    asset_class: str,
    strategy_version: int,
    intended_notional_usd: Decimal,
    wallet_id: UUID,
    closing: bool = False,
) -> GateDecision:
    reasons: list[str] = []
    snapshot: dict[str, object] = {
        "strategy_id": strategy_id,
        "asset_class": asset_class,
        "strategy_version": strategy_version,
        "intended_notional_usd": str(intended_notional_usd),
        "wallet_id": str(wallet_id),
        "closing": closing,
        "ts": datetime.now(timezone.utc).isoformat(),
    }

    posture = mainnet_refusal_reasons()
    snapshot["mainnet_refusal"] = posture
    reasons.extend(posture)

    live_enabled = env_live_enabled()
    snapshot["live_execution_enabled"] = live_enabled
    if not live_enabled:
        reasons.append("LIVE_EXECUTION_ENABLED is not 'true'")

    if LIVE_EXECUTION_REQUIRES_CERT:
        has_cert = await has_valid_certificate(strategy_id, asset_class, strategy_version)
        snapshot["has_valid_certificate"] = has_cert
        if not has_cert:
            reasons.append(
                f"no valid paper_trade_certificate for {strategy_id}/{asset_class}/v{strategy_version}"
            )

    async with shared_session_scope() as session:
        wallet = await session.get(Wallet, wallet_id)
        if wallet is None:
            reasons.append(f"wallet {wallet_id} not found")
            return GateDecision(allowed=False, reasons=reasons, snapshot=snapshot)

        snapshot["wallet_cash_usd"] = str(wallet.cash_usd)
        snapshot["wallet_locked_usd"] = str(wallet.locked_usd)
        snapshot["wallet_max_position_pct"] = str(wallet.max_position_pct)
        snapshot["wallet_circuit_tripped_at"] = (
            wallet.circuit_tripped_at.isoformat() if wallet.circuit_tripped_at is not None else None
        )
        if wallet.circuit_tripped_at is not None and not closing:
            reasons.append("wallet daily-loss circuit is tripped")

        if closing:
            return GateDecision(allowed=len(reasons) == 0, reasons=reasons, snapshot=snapshot)

        equity = Decimal(wallet.cash_usd) + Decimal(wallet.locked_usd)
        max_notional = equity * Decimal(wallet.max_position_pct)
        snapshot["equity_usd"] = str(equity)
        snapshot["max_notional_usd"] = str(max_notional)
        if intended_notional_usd > max_notional:
            reasons.append(
                f"intended_notional_usd={intended_notional_usd} > max_position_pct * equity = {max_notional}"
            )

        n_open = (await session.execute(
            select(func.count(PaperPosition.id))
            .where(PaperPosition.wallet_id == wallet.id)
            .where(PaperPosition.status == "open")
        )).scalar_one()
        snapshot["n_open_positions"] = n_open
        snapshot["max_concurrent_positions"] = wallet.max_concurrent_positions
        if n_open >= wallet.max_concurrent_positions:
            reasons.append(f"already {n_open}/{wallet.max_concurrent_positions} positions open")

        cap = env_capital_cap_usd()
        snapshot["live_capital_cap_usd"] = str(cap)
        projected_locked = Decimal(wallet.locked_usd) + intended_notional_usd
        if projected_locked > cap:
            reasons.append(f"projected locked_usd={projected_locked} > LIVE_CAPITAL_CAP_USD={cap}")

        slot_cfg = await session.get(StrategySlotConfig, (strategy_id, asset_class, wallet_id))
        if slot_cfg is not None:
            open_for_strategy = (await session.execute(
                select(func.count(PaperPosition.id))
                .join(Prediction, Prediction.id == PaperPosition.prediction_id)
                .where(PaperPosition.wallet_id == wallet_id)
                .where(PaperPosition.status == "open")
                .where(Prediction.strategy_id == strategy_id)
            )).scalar_one()
            snapshot["strategy_open_positions"] = open_for_strategy
            snapshot["strategy_allocated_slots"] = slot_cfg.allocated_slots
            if open_for_strategy >= slot_cfg.allocated_slots:
                reasons.append(
                    f"strategy {strategy_id} at slot cap ({open_for_strategy}/{slot_cfg.allocated_slots})"
                )
        else:
            snapshot["strategy_allocated_slots"] = "unregistered"

    return GateDecision(allowed=len(reasons) == 0, reasons=reasons, snapshot=snapshot)

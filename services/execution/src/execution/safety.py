"""Composite live-execution gate.

`should_submit_live(...)` is the ONLY function callers should consult
before sending a real-money order. It collects every safety check that
docs/TRADING.md requires and reports every reason an order would be
rejected, not just the first one — that's deliberate, so operators can
see the full picture at a glance instead of fixing one gate to discover
another behind it.

Phase 0 status: this skeleton handles four layers. Broker submission code
does NOT exist yet. When it lands, it MUST call should_submit_live and
ABORT if `allowed=False`. There is no path that bypasses this function
for live capital.

Layers, in order of cheapness to evaluate:
    1. LIVE_EXECUTION_ENABLED env flag (default false; manual edit only)
    2. paper_trade_certificate gate (matrix_shared.has_valid_certificate)
    3. Wallet circuit_tripped_at (daily loss circuit breaker)
    4. Per-trade position cap (notional <= equity * max_position_pct)
    5. Wallet hasn't exceeded LIVE_CAPITAL_CAP_USD (env ceiling)
    6. max_concurrent_positions cap

Notes on what's NOT here:
- We don't load BROKER_API_KEY or talk to any exchange. That's Phase 5
  + a separate connector file. The gate must stay simple and pure.
- We don't decide *what* to trade. That's the agent + strategy layers.
  Execution only judges whether the proposed trade is *allowed*.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select

from matrix_shared import (
    LIVE_EXECUTION_REQUIRES_CERT,
    has_valid_certificate,
    shared_session_scope,
)
from matrix_shared.models import PaperPosition, Wallet


@dataclass(slots=True)
class GateDecision:
    allowed: bool
    reasons: list[str]
    # Snapshot of the inputs we evaluated against, for audit logs.
    snapshot: dict[str, object]


# Hardcoded sentinel matching matrix_shared.trading_safety. Repeated here
# so a casual reader of execution code sees the wall without an indirect
# import chase. Both must stay in sync; if they ever disagree the safer
# behavior wins (i.e. require=True).
LIVE_EXECUTION_REQUIRES_CERT_LOCAL = True


def _env_live_enabled() -> bool:
    """LIVE_EXECUTION_ENABLED=true ONLY in .env.local that the operator
    explicitly edited. Anything else (unset, '', 'false', etc.) is no."""
    return os.environ.get("LIVE_EXECUTION_ENABLED", "false").strip().lower() == "true"


def _env_capital_cap_usd() -> Decimal:
    raw = os.environ.get("LIVE_CAPITAL_CAP_USD", "2000")
    try:
        return Decimal(str(raw))
    except (ValueError, ArithmeticError):
        # Refuse to live-trade with a bogus cap; treat as zero (most
        # restrictive). The gate will then deny on the cap check.
        return Decimal("0")


async def should_submit_live(
    *,
    strategy_id: str,
    asset_class: str,
    strategy_version: int,
    intended_notional_usd: Decimal,
    wallet_id: UUID,
) -> GateDecision:
    """Composite live-order gate. Returns (allowed, [reasons], snapshot).

    Callers should ASSERT `allowed is True` before submitting. Logs/audits
    should record `snapshot` regardless of outcome — denied attempts are
    the most interesting events.
    """
    reasons: list[str] = []
    snapshot: dict[str, object] = {
        "strategy_id": strategy_id,
        "asset_class": asset_class,
        "strategy_version": strategy_version,
        "intended_notional_usd": str(intended_notional_usd),
        "wallet_id": str(wallet_id),
        "ts": datetime.now(timezone.utc).isoformat(),
    }

    # 1. Global on/off flag (operator-controlled, NEVER set by code)
    live_enabled = _env_live_enabled()
    snapshot["live_execution_enabled"] = live_enabled
    if not live_enabled:
        reasons.append("LIVE_EXECUTION_ENABLED is not 'true'")

    # 2. Certificate gate (Phase 5 wall)
    if LIVE_EXECUTION_REQUIRES_CERT and LIVE_EXECUTION_REQUIRES_CERT_LOCAL:
        has_cert = await has_valid_certificate(
            strategy_id, asset_class, strategy_version
        )
        snapshot["has_valid_certificate"] = has_cert
        if not has_cert:
            reasons.append(
                f"no valid paper_trade_certificate for "
                f"{strategy_id}/{asset_class}/v{strategy_version}"
            )

    # 3 + 4 + 6. Wallet-level checks
    async with shared_session_scope() as session:
        wallet = await session.get(Wallet, wallet_id)
        if wallet is None:
            reasons.append(f"wallet {wallet_id} not found")
            return GateDecision(allowed=False, reasons=reasons, snapshot=snapshot)

        snapshot["wallet_cash_usd"] = str(wallet.cash_usd)
        snapshot["wallet_locked_usd"] = str(wallet.locked_usd)
        snapshot["wallet_max_position_pct"] = str(wallet.max_position_pct)
        snapshot["wallet_circuit_tripped_at"] = (
            wallet.circuit_tripped_at.isoformat()
            if wallet.circuit_tripped_at is not None
            else None
        )

        # 3. Daily-loss circuit breaker
        if wallet.circuit_tripped_at is not None:
            reasons.append("wallet daily-loss circuit is tripped")

        # 4. Per-trade position cap
        equity = Decimal(wallet.cash_usd) + Decimal(wallet.locked_usd)
        max_notional = equity * Decimal(wallet.max_position_pct)
        snapshot["equity_usd"] = str(equity)
        snapshot["max_notional_usd"] = str(max_notional)
        if intended_notional_usd > max_notional:
            reasons.append(
                f"intended_notional_usd={intended_notional_usd} > "
                f"max_position_pct * equity = {max_notional}"
            )

        # 6. Concurrent-position cap
        n_open = (
            await session.execute(
                select(func.count(PaperPosition.id))
                .where(PaperPosition.wallet_id == wallet.id)
                .where(PaperPosition.status == "open")
            )
        ).scalar_one()
        snapshot["n_open_positions"] = n_open
        snapshot["max_concurrent_positions"] = wallet.max_concurrent_positions
        if n_open >= wallet.max_concurrent_positions:
            reasons.append(
                f"already {n_open}/{wallet.max_concurrent_positions} positions open"
            )

    # 5. LIVE_CAPITAL_CAP_USD (env ceiling on capital ever exposed)
    cap = _env_capital_cap_usd()
    snapshot["live_capital_cap_usd"] = str(cap)
    # `locked + intended` is the new exposed-capital floor; if even that
    # exceeds the cap, refuse. (cash is held back as buffer.)
    projected_locked = Decimal(wallet.locked_usd) + intended_notional_usd
    if projected_locked > cap:
        reasons.append(
            f"projected locked_usd={projected_locked} > LIVE_CAPITAL_CAP_USD={cap}"
        )

    return GateDecision(
        allowed=len(reasons) == 0,
        reasons=reasons,
        snapshot=snapshot,
    )

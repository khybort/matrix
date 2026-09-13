"""Composite live-execution gate.

`should_submit_live(...)` is the ONLY function callers should consult
before sending a real-money order. It collects every safety check that
docs/TRADING.md requires and reports every reason an order would be
rejected, not just the first one — that's deliberate, so operators can
see the full picture at a glance instead of fixing one gate to discover
another behind it.

The implementation lives in `matrix_shared.live_gate` (2026-09-13) so the
paper engine's `exchange_shadow` mirror runs the identical checks; this
module is the execution-service facade. Any broker submission code MUST
call should_submit_live and ABORT if `allowed=False`.

Layers, in order of cheapness to evaluate:
    0. Deployment posture (mainnet + MATRIX_CERT_* overrides → refuse)
    1. LIVE_EXECUTION_ENABLED env flag (default false; manual edit only)
    2. paper_trade_certificate gate (matrix_shared.has_valid_certificate)
    3. Wallet circuit_tripped_at (daily loss circuit breaker)
    4. Per-trade position cap (notional <= equity * max_position_pct)
    5. Wallet hasn't exceeded LIVE_CAPITAL_CAP_USD (env ceiling)
    6. max_concurrent_positions cap
    7. Per-strategy slot cap (via strategy_slot_configs table)

Notes on what's NOT here:
- We don't load BROKER_API_KEY or talk to any exchange. That's Phase 5
  + a separate connector file. The gate must stay simple and pure.
- We don't decide *what* to trade. That's the agent + strategy layers.
  Execution only judges whether the proposed trade is *allowed*.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from matrix_shared.live_gate import (  # noqa: F401 — re-exported for callers/tests
    GateDecision,
    env_capital_cap_usd as _env_capital_cap_usd,
    env_live_enabled as _env_live_enabled,
)
from matrix_shared.live_gate import should_submit_live as _shared_gate

# Hardcoded sentinel kept for readers of execution code; the shared gate
# imports the authoritative constant from matrix_shared.trading_safety.
LIVE_EXECUTION_REQUIRES_CERT_LOCAL = True


async def should_submit_live(
    *,
    strategy_id: str,
    asset_class: str,
    strategy_version: int,
    intended_notional_usd: Decimal,
    wallet_id: UUID,
    closing: bool = False,
) -> GateDecision:
    """Composite live-order gate — delegates to `matrix_shared.live_gate`, the
    single implementation shared with the paper engine's exchange mirror."""
    return await _shared_gate(
        strategy_id=strategy_id,
        asset_class=asset_class,
        strategy_version=strategy_version,
        intended_notional_usd=intended_notional_usd,
        wallet_id=wallet_id,
        closing=closing,
    )

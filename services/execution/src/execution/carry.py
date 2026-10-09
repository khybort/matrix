"""Two-leg carry execution — execution-service facade.

The implementation lives in `matrix_shared.carry_executor` so the paper
engine's dry-run shadow mirror runs the identical state machine (the same
reason `matrix_shared.live_gate` is shared). Every order path in it goes
through `should_submit_live` with the combined notional of both legs.
Design: docs/wiki/carry-execution.md.
"""

from __future__ import annotations

from matrix_shared.carry_executor import (  # noqa: F401 — re-exported
    DRY_RUN,
    TESTNET,
    CarryExecutor,
    CarryIntent,
    CarryResult,
    kill_switch_reset,
    kill_switch_state,
)

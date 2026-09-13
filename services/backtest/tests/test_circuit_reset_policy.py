"""Circuit breaker reset policy (pure): paper auto-resets at day roll; live does not."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from matrix_shared.models import Wallet

from backtest.paper_trade import _check_and_maybe_reset_day

pytestmark = pytest.mark.asyncio


def _tripped_wallet() -> Wallet:
    return Wallet(
        name="default", asset_class="crypto", starting_capital_usd=Decimal("10000"),
        cash_usd=Decimal("9000"), locked_usd=Decimal("0"), max_position_pct=Decimal("0.02"),
        max_concurrent_positions=5, daily_loss_circuit_pct=Decimal("0.05"),
        day_start_equity=Decimal("10000"), day_start_at=datetime.now(UTC) - timedelta(days=1),
        circuit_tripped_at=datetime.now(UTC) - timedelta(hours=20),
    )


async def test_paper_mode_resets_circuit_at_day_roll(monkeypatch):
    monkeypatch.setenv("LIVE_EXECUTION_ENABLED", "false")
    w = _tripped_wallet()
    await _check_and_maybe_reset_day(w, Decimal("9000"))
    assert w.circuit_tripped_at is None
    assert w.day_start_equity == Decimal("9000")


async def test_live_mode_keeps_circuit_tripped(monkeypatch):
    monkeypatch.setenv("LIVE_EXECUTION_ENABLED", "true")
    w = _tripped_wallet()
    await _check_and_maybe_reset_day(w, Decimal("9000"))
    assert w.circuit_tripped_at is not None
    assert w.day_start_equity == Decimal("9000")  # day still rolls

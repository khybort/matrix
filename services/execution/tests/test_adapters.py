"""ExecutionAdapter contract tests for crypto + BIST.

DB-free; instantiates the adapters via MarketAdapter factories and
verifies the Phase D contract:
- Both adapters implement the ABC (subclass + required methods).
- Crypto live executor instantiates without env / network access.
- BIST live executor instantiates but every operation raises
  NotImplementedError (Phase 1 wall).
- Paper-mode factory raises NotImplementedError for both markets
  (paper-trade engine lives in services/backtest, not here).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from matrix_shared.markets import ExecutionAdapter, get_market

# Side-effect import: lets matrix_shared.markets factories late-bind.
import execution.adapters  # noqa: F401
from execution.adapters import BistLiveExecutor, CryptoLiveExecutor


@dataclass(slots=True)
class _Cfg:
    testnet: bool = True


def test_crypto_executor_implements_abc() -> None:
    e = get_market("crypto").make_executor(_Cfg(), paper=False)
    assert isinstance(e, ExecutionAdapter)
    assert isinstance(e, CryptoLiveExecutor)
    assert e.testnet is True


def test_bist_executor_implements_abc() -> None:
    e = get_market("bist").make_executor(_Cfg(), paper=False)
    assert isinstance(e, ExecutionAdapter)
    assert isinstance(e, BistLiveExecutor)


def test_crypto_paper_factory_raises() -> None:
    with pytest.raises(NotImplementedError, match="services/backtest"):
        get_market("crypto").make_executor(_Cfg(), paper=True)


def test_bist_paper_factory_raises() -> None:
    with pytest.raises(NotImplementedError, match="services/backtest"):
        get_market("bist").make_executor(_Cfg(), paper=True)


def test_bist_live_methods_wall() -> None:
    e = BistLiveExecutor()
    from decimal import Decimal

    async def _call_each() -> None:
        with pytest.raises(NotImplementedError, match="Phase 1"):
            await e.place_order(symbol="THYAO", side="buy", qty=Decimal("1"))
        with pytest.raises(NotImplementedError, match="Phase 1"):
            await e.cancel_order(order_id="x")
        with pytest.raises(NotImplementedError, match="Phase 1"):
            await e.positions()
        with pytest.raises(NotImplementedError, match="Phase 1"):
            await e.equity()
        # health returns False (never raises) — the daemon polls this.
        assert await e.health() is False

    asyncio.run(_call_each())

"""Per-market live-execution adapters.

Each module here implements `matrix_shared.markets.ExecutionAdapter` for
one venue family. The execution daemon (`execution.main`) walks
`all_markets()` and drives the corresponding adapter — no hard-coded
"crypto-only" path remains.

Phase 0 status (2026-05-26): only `CryptoLiveExecutor` wraps a real
broker (Bybit). `BistLiveExecutor` raises NotImplementedError — wired
in Phase 1 once a BIST broker (AlgoLab / Garanti API / …) is chosen.
Paper-trade simulation lives in `services/backtest/` and is not an
ExecutionAdapter — Wallet/PaperPosition tables already model that.
"""

from __future__ import annotations

from execution.adapters.bist import BistLiveExecutor
from execution.adapters.crypto import CryptoLiveExecutor

__all__ = ["BistLiveExecutor", "CryptoLiveExecutor"]

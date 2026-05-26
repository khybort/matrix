"""Pluggable market adapters.

Importing this package side-effect-registers every concrete adapter
(crypto, bist, …) into the global registry. Consumers should depend on
`MarketAdapter` and the registry helpers, never on a specific market.
"""

from __future__ import annotations

from .base import (
    ExecutionAdapter,
    FeeModel,
    IngestorAdapter,
    MarketAdapter,
)
from .registry import all_markets, get_market, infer_market, register

# Side-effect: register concrete adapters. Order matters only for
# disambiguation in infer_market (which raises on ambiguity anyway).
from . import bist as _bist  # noqa: F401
from . import crypto as _crypto  # noqa: F401

__all__ = [
    "ExecutionAdapter",
    "FeeModel",
    "IngestorAdapter",
    "MarketAdapter",
    "all_markets",
    "get_market",
    "infer_market",
    "register",
]

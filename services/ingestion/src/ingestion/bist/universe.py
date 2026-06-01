"""BIST symbol metadata types — runtime universe lives in `bist_symbols`."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SymbolSeed:
    """One BIST equity ticker (no Yahoo `.IS` suffix)."""

    symbol: str
    name: str | None = None
    sector: str | None = None
    index_membership: str | None = None  # e.g. "BIST30,BIST100"

"""Per-market ingestion adapters (IngestorAdapter implementations).

Concrete classes live alongside their data source code (bybit websocket
for crypto, yfinance polling for BIST). The `ingestion.main` daemon
walks `all_markets()` and runs every adapter in parallel as an asyncio
task — same container, same env, same restart policy.
"""

from __future__ import annotations

from ingestion.adapters.bist import BistIngestor
from ingestion.adapters.crypto import CryptoIngestor

__all__ = ["BistIngestor", "CryptoIngestor"]

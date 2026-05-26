"""BIST IngestorAdapter — yfinance bar poller, packaged for the unified
ingestion daemon (Phase F)."""

from __future__ import annotations

from loguru import logger

from matrix_shared.markets import IngestorAdapter

from ingestion.bist.bars import run as _run_bist_bars


class BistIngestor(IngestorAdapter):
    """Long-running BIST bars poller.

    Delegates to `ingestion.bist.bars.run` which already owns the
    intraday-vs-EOD cadence, batching, and TR-session gating. Wrapped
    here so the unified ingestion main can treat all markets symmetrically.
    """

    async def run(self) -> None:
        logger.info("[bist] ingest start: yfinance bar poller")
        await _run_bist_bars()

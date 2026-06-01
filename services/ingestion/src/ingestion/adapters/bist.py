"""BIST IngestorAdapter — discover + yfinance bar poller."""

from __future__ import annotations

import asyncio
import os

from loguru import logger

from matrix_shared.markets import IngestorAdapter

from ingestion.bist.bars import run as _run_bist_bars
from ingestion.bist.discover import refresh_universe

_DISCOVER_INTERVAL_S = float(os.environ.get("BIST_DISCOVER_INTERVAL_S", "86400"))


class BistIngestor(IngestorAdapter):
    async def run(self) -> None:
        logger.info("[bist] ingest start: discover + yfinance bar poller")
        try:
            await refresh_universe()
        except Exception as e:
            logger.warning(f"[bist] initial discover failed (retry in loop): {e}")

        async def _discover_loop() -> None:
            while True:
                await asyncio.sleep(_DISCOVER_INTERVAL_S)
                try:
                    await refresh_universe()
                except Exception as e:
                    logger.warning(f"[bist] periodic discover failed: {e}")

        discover_task = asyncio.create_task(_discover_loop(), name="bist-discover")
        try:
            await _run_bist_bars()
        finally:
            discover_task.cancel()
            await asyncio.gather(discover_task, return_exceptions=True)

"""Crypto IngestorAdapter — wraps the existing BybitConnector + persist loop.

The ingestor periodically reconciles its active symbol set against
`tradable_symbols` (UNIVERSE_RECONCILE_INTERVAL_S, default 300s). When the
active set changes it cancels the current connector task and restarts with the
new set — a controlled session bounce. Mirrors bist/bars.py's reconcile pattern
(lowest risk; live WS unsubscribe/resubscribe is a follow-up).

Set UNIVERSE_MANAGER_ENFORCE=true to activate dynamic universe management.
Without it, crypto_universe() still falls back to _DEFAULT_UNIVERSE.
"""

from __future__ import annotations

import asyncio
import os
import time

from loguru import logger

from matrix_shared.markets import IngestorAdapter
from matrix_shared.markets.crypto import crypto_universe

from ingestion.connectors.bybit import BybitConnector
from ingestion.persist import persist_events

RECONCILE_INTERVAL_S = float(
    os.environ.get("UNIVERSE_RECONCILE_INTERVAL_S", "300")
)


def _default_symbols() -> list[str]:
    # Single source of truth — same set the strategies / agent trade, so we
    # never stream a symbol nothing analyzes or analyze a symbol we don't
    # stream. Override via CRYPTO_SYMBOLS.
    return crypto_universe()


class CryptoIngestor(IngestorAdapter):
    def __init__(
        self,
        symbols: list[str] | None = None,
        *,
        testnet: bool | None = None,
    ) -> None:
        self._init_symbols = list(symbols) if symbols else None
        if testnet is None:
            testnet = (
                os.environ.get("BYBIT_TESTNET", "true").strip().lower() != "false"
            )
        self.testnet = testnet

    async def run(self) -> None:
        current_symbols = self._init_symbols or _default_symbols()
        last_reconcile = time.monotonic()

        while True:
            logger.info(
                f"[crypto] ingest start: symbols={current_symbols} testnet={self.testnet}"
            )
            connector = BybitConnector(current_symbols, testnet=self.testnet)
            stream_task = asyncio.create_task(
                persist_events(connector.stream()), name="crypto_stream"
            )

            try:
                while not stream_task.done():
                    await asyncio.sleep(min(10.0, RECONCILE_INTERVAL_S))

                    now = time.monotonic()
                    if now - last_reconcile < RECONCILE_INTERVAL_S:
                        continue
                    last_reconcile = now

                    new_symbols = _default_symbols()
                    if sorted(new_symbols) != sorted(current_symbols):
                        logger.info(
                            f"[crypto] universe changed: "
                            f"{sorted(current_symbols)} -> {sorted(new_symbols)}; "
                            "reconnecting"
                        )
                        stream_task.cancel()
                        try:
                            await stream_task
                        except asyncio.CancelledError:
                            pass
                        current_symbols = new_symbols
                        break  # restart outer loop with new symbols
            except asyncio.CancelledError:
                stream_task.cancel()
                try:
                    await stream_task
                except asyncio.CancelledError:
                    pass
                return

            # If stream_task finished on its own (connector error / EOF) restart.
            exc = stream_task.exception() if not stream_task.cancelled() else None
            if exc:
                logger.warning(f"[crypto] stream ended with error: {exc}; restarting")
            await asyncio.sleep(2.0)  # brief pause before reconnect

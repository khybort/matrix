"""Crypto IngestorAdapter — wraps the existing BybitConnector + persist loop."""

from __future__ import annotations

import os

from loguru import logger

from matrix_shared.markets import IngestorAdapter
from matrix_shared.markets.crypto import crypto_universe

from ingestion.connectors.bybit import BybitConnector
from ingestion.persist import persist_events


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
        self.symbols = list(symbols) if symbols else _default_symbols()
        if testnet is None:
            testnet = (
                os.environ.get("BYBIT_TESTNET", "true").strip().lower() != "false"
            )
        self.testnet = testnet

    async def run(self) -> None:
        logger.info(
            f"[crypto] ingest start: symbols={self.symbols} testnet={self.testnet}"
        )
        connector = BybitConnector(self.symbols, testnet=self.testnet)
        await persist_events(connector.stream())

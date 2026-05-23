"""Ingestion service entry point.

Usage:
    uv run python -m ingestion.main                       # default: BTCUSDT
    uv run python -m ingestion.main BTCUSDT ETHUSDT       # custom symbols
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys

from loguru import logger

from ingestion.connectors.bybit import BybitConnector
from ingestion.persist import persist_events

DEFAULT_SYMBOLS = ["BTCUSDT"]


async def run(symbols: list[str], *, testnet: bool) -> None:
    connector = BybitConnector(symbols, testnet=testnet)

    stop = asyncio.Event()

    def _handle_signal(*_: object) -> None:
        logger.info("shutdown signal received")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal)

    async def _stream_with_stop():
        async for event in connector.stream():
            if stop.is_set():
                return
            yield event

    try:
        await persist_events(_stream_with_stop())
    except asyncio.CancelledError:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Matrix market ingestion")
    parser.add_argument(
        "symbols",
        nargs="*",
        default=DEFAULT_SYMBOLS,
        help=f"Symbols to subscribe (default: {DEFAULT_SYMBOLS})",
    )
    parser.add_argument(
        "--mainnet",
        action="store_true",
        help="Use mainnet (default: testnet). DO NOT enable casually.",
    )
    args = parser.parse_args()

    logger.remove()
    logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level: <5} | {message}")

    testnet = not args.mainnet
    if not testnet:
        logger.warning("MAINNET mode — recording live data, no execution yet but be aware")

    logger.info(f"ingestion start: symbols={args.symbols} testnet={testnet}")
    asyncio.run(run(args.symbols, testnet=testnet))


if __name__ == "__main__":
    main()

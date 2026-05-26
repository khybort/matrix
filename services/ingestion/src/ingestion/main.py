"""Unified ingestion daemon (Phase F).

Walks `matrix_shared.markets.all_markets()` and runs every market's
IngestorAdapter as a parallel asyncio task. One container, one log
stream, one restart policy — no more separate `bist-ingestion` service.

Usage:
    uv run python -m ingestion.main                     # all markets
    uv run python -m ingestion.main --markets crypto    # one market only
    uv run python -m ingestion.main --symbols BTCUSDT ETHUSDT   # crypto override

Crypto symbols can still be overridden via CLI or `CRYPTO_SYMBOLS` env;
BIST universe comes from `bist_symbols` (run `matrix-bist-symbols`).
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from dataclasses import dataclass

from loguru import logger

from matrix_shared.markets import all_markets, get_market

# Side-effect import so MarketAdapter factories can late-bind to our
# IngestorAdapter subclasses.
import ingestion.adapters  # noqa: F401


@dataclass(slots=True)
class _IngestCfg:
    symbols: list[str] | None
    testnet: bool


async def _run_market(market_name: str, cfg: _IngestCfg) -> None:
    market = get_market(market_name)
    try:
        ingestor = market.make_ingestor(cfg)
    except (NotImplementedError, RuntimeError) as e:
        logger.warning(f"[{market_name}] ingestor not wired: {e}")
        return
    try:
        await ingestor.run()
    except asyncio.CancelledError:
        logger.info(f"[{market_name}] ingestor cancelled")
        raise
    except Exception as e:  # noqa: BLE001
        logger.exception(f"[{market_name}] ingestor crashed: {e}")


async def run(market_names: list[str], cfg: _IngestCfg) -> None:
    stop = asyncio.Event()

    def _handle_signal(*_: object) -> None:
        logger.info("shutdown signal received")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal)

    tasks = [
        asyncio.create_task(_run_market(name, cfg), name=f"ingest-{name}")
        for name in market_names
    ]
    if not tasks:
        logger.warning("no markets to ingest; exiting")
        return

    # Wait for either a stop signal or any ingestor exit (crashes propagate
    # via logs above; we don't restart inside the same process — let the
    # container restart policy do that).
    done_task = asyncio.create_task(stop.wait(), name="stop-wait")
    await asyncio.wait(
        [*tasks, done_task],
        return_when=asyncio.FIRST_COMPLETED,
    )
    for t in tasks:
        if not t.done():
            t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Matrix unified ingestion daemon")
    parser.add_argument(
        "--markets",
        nargs="*",
        default=None,
        help="Markets to ingest (default: all registered)",
    )
    parser.add_argument(
        "--symbols",
        nargs="*",
        default=None,
        help="Crypto symbol override (other markets use their adapter's universe)",
    )
    parser.add_argument(
        "--mainnet",
        action="store_true",
        help="Use mainnet for crypto (default: testnet). DO NOT enable casually.",
    )
    args = parser.parse_args()

    logger.remove()
    logger.add(
        sys.stderr,
        level="INFO",
        format="{time:HH:mm:ss} | {level: <5} | {message}",
    )

    market_names = args.markets if args.markets else [m.name for m in all_markets()]
    testnet = not args.mainnet
    if not testnet:
        logger.warning(
            "MAINNET mode (crypto) — recording live data, no execution yet but be aware"
        )

    cfg = _IngestCfg(symbols=args.symbols, testnet=testnet)
    logger.info(
        f"ingestion start: markets={market_names} crypto_symbols={args.symbols} "
        f"testnet={testnet}"
    )

    asyncio.run(run(market_names, cfg))


if __name__ == "__main__":
    main()

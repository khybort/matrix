"""Decision-agent main loop.

For each tick:
    1. For each tracked symbol, extract features
    2. Run decide() → Decision
    3. If side != hold, persist a Prediction (strategy_id="matrix_agent")

The agent is just another strategy from the paper-trade engine's perspective —
its predictions land in the same `predictions` table and are scored the same way.

Usage:
    uv run python -m agent.main                          # default 15s loop, BTCUSDT+ETHUSDT
    uv run python -m agent.main --interval 10
    uv run python -m agent.main --symbols BTCUSDT ETHUSDT SOLUSDT
    uv run python -m agent.main --once
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from loguru import logger

from matrix_shared import session_scope
from matrix_shared.models import Prediction

from agent.config import load_agent_config
from agent.decide import decide
from agent.features import extract_symbol_features

AGENT_STRATEGY_ID = "matrix_agent"

DEFAULT_INTERVAL_S = 15.0
DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT"]


async def _tick(symbols: list[str]) -> int:
    """Run one decision cycle. Returns number of non-hold predictions persisted."""
    cfg = await load_agent_config(AGENT_STRATEGY_ID)
    persisted = 0
    for symbol in symbols:
        try:
            features = await extract_symbol_features(symbol)
            decision = await decide(features, cfg)
        except Exception as e:
            logger.exception(f"agent error for {symbol}: {e}")
            continue

        if decision.side == "hold" or decision.last_price is None:
            logger.info(f"{symbol}: HOLD ({decision.thesis})")
            continue
        if decision.confidence < Decimal("0.1"):
            logger.info(f"{symbol}: skip; conf too low ({decision.confidence:.3f})")
            continue

        now = datetime.now(UTC)
        async with session_scope() as session:
            session.add(
                Prediction(
                    strategy_id=AGENT_STRATEGY_ID,
                    strategy_version=cfg.version,
                    generated_at=now,
                    symbol=symbol,
                    exchange="bybit",  # agent operates on whatever ingestion provides
                    side=decision.side,
                    confidence=decision.confidence,
                    horizon_seconds=cfg.horizon_seconds,
                    close_by=now + timedelta(seconds=cfg.horizon_seconds),
                    entry_price_ref=decision.last_price,
                    thesis=decision.thesis,
                    context={**decision.feature_dump, "agent_version": cfg.version},
                    status="open",
                )
            )
        persisted += 1
        logger.info(
            f"{symbol} v{cfg.version}: {decision.side.upper()} conf={decision.confidence:.3f} "
            f"method={decision.method} | {decision.thesis[:120]}"
        )

    return persisted


async def run(symbols: list[str], interval_s: float) -> None:
    stop = asyncio.Event()

    def _handle_signal(*_: object) -> None:
        logger.info("shutdown signal received")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal)

    while not stop.is_set():
        try:
            n = await _tick(symbols)
            if n:
                logger.info(f"tick: persisted {n} predictions")
        except Exception as e:
            logger.exception(f"tick failed: {e}")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
        except TimeoutError:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Matrix decision agent")
    parser.add_argument("--symbols", nargs="*", default=DEFAULT_SYMBOLS)
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL_S,
        help=f"Loop interval seconds (default {DEFAULT_INTERVAL_S})",
    )
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    logger.remove()
    logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level: <5} | {message}")
    logger.info(
        f"agent start: symbols={args.symbols} interval={args.interval}s once={args.once}"
    )

    if args.once:
        asyncio.run(_tick(args.symbols))
    else:
        asyncio.run(run(args.symbols, args.interval))


if __name__ == "__main__":
    main()

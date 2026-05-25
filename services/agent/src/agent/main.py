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
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from loguru import logger
from sqlalchemy import select

from matrix_shared import session_scope, shared_session_scope
from matrix_shared.models import BistSymbol, Prediction

from agent.config import load_agent_config
from agent.decide import decide
from agent.features import extract_symbol_features

AGENT_STRATEGY_ID = "matrix_agent"

DEFAULT_INTERVAL_S = 15.0
DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT"]

TR = ZoneInfo("Europe/Istanbul")
BIST_OPEN = time(10, 0)
BIST_CLOSE = time(18, 0)


def _bist_in_session(now: datetime | None = None) -> bool:
    now = (now or datetime.now(TR)).astimezone(TR)
    if now.weekday() >= 5:
        return False
    return BIST_OPEN <= now.time() < BIST_CLOSE


async def _bist_symbols() -> list[str]:
    async with session_scope() as session:
        rows = await session.execute(
            select(BistSymbol.symbol).where(BistSymbol.active.is_(True))
        )
    return sorted({r[0] for r in rows})


def _exchange_for(asset_class: str) -> str:
    return "BIST" if asset_class == "bist" else "bybit"


async def _tick(symbols: list[str]) -> int:
    """Run one decision cycle. Returns number of non-hold predictions persisted.

    `symbols` is the crypto universe (typically passed via --symbols). BIST
    symbols are loaded fresh from the DB and only processed while the TR
    session is open. Each symbol is paired with its asset_class so the
    decision layer can drop crypto-only signals where appropriate.
    """
    targets: list[tuple[str, str]] = [(s, "crypto") for s in symbols]
    if _bist_in_session():
        bist = await _bist_symbols()
        targets.extend((s, "bist") for s in bist)

    # Config per asset_class (cached). Pre-load both so we don't re-query
    # every symbol within the same tick.
    cfgs = {ac: await load_agent_config(AGENT_STRATEGY_ID, ac) for ac in {ac for _, ac in targets}}

    persisted = 0
    for symbol, asset_class in targets:
        cfg = cfgs[asset_class]
        try:
            features = await extract_symbol_features(symbol)
            decision = await decide(features, cfg, asset_class=asset_class)
        except Exception as e:
            logger.exception(f"agent error for {symbol} ({asset_class}): {e}")
            continue

        if decision.side == "hold" or decision.last_price is None:
            logger.debug(f"{symbol} [{asset_class}]: HOLD ({decision.thesis[:80]})")
            continue
        if decision.confidence < Decimal("0.1"):
            logger.debug(
                f"{symbol} [{asset_class}]: skip; conf={decision.confidence:.3f}"
            )
            continue

        now = datetime.now(UTC)
        async with shared_session_scope() as session:
            session.add(
                Prediction(
                    strategy_id=AGENT_STRATEGY_ID,
                    strategy_version=cfg.version,
                    generated_at=now,
                    symbol=symbol,
                    exchange=_exchange_for(asset_class),
                    asset_class=asset_class,
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
            f"{symbol} [{asset_class}] v{cfg.version}: {decision.side.upper()} "
            f"conf={decision.confidence:.3f} method={decision.method}"
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

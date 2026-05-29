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
from sqlalchemy import select

from matrix_shared import session_scope, shared_session_scope
from matrix_shared.markets import all_markets
from matrix_shared.models import Prediction, TradableSymbol

from agent.config import load_agent_config
from agent.decide import decide
from agent.features import extract_symbol_features

AGENT_STRATEGY_ID = "matrix_agent"

DEFAULT_INTERVAL_S = 15.0


def _exchange_for(asset_class: str) -> str:
    return "BIST" if asset_class == "bist" else "bybit"


_QUOTE_SUFFIXES = ("USDT", "USDC", "BUSD", "USD")


def _asset_canonical(symbol: str, asset_class: str) -> str:
    """Map a trading symbol to the graph Asset canonical (e.g. BTCUSDT→BTC).

    Crypto strips the quote-currency suffix to align with the entity
    extractor's canonicals (BTC, ETH, ...). Other classes use the symbol
    as-is.
    """
    if asset_class != "crypto":
        return symbol
    for suf in _QUOTE_SUFFIXES:
        if symbol.endswith(suf) and len(symbol) > len(suf):
            return symbol[: -len(suf)]
    return symbol


async def _mirror_prediction_to_graph(
    pred_id: str,
    symbol: str,
    asset_class: str,
    decision,
    version: int,
    is_exploration: bool,
) -> None:
    """Best-effort overlay write: a Prediction node on the LOCAL AGE graph.

    The relational row (SHARED) is the source of truth; this is an index for
    graph traversal. Never let a graph failure affect the trading loop.
    """
    try:
        from graph.overlay import upsert_prediction_node

        await upsert_prediction_node(
            pred_id=pred_id,
            symbol=symbol,
            side=decision.side,
            confidence=str(decision.confidence),
            strategy_id=AGENT_STRATEGY_ID,
            asset_class=asset_class,
            is_exploration=is_exploration,
            asset_canonical=_asset_canonical(symbol, asset_class),
            strategy_key=f"{AGENT_STRATEGY_ID}:{version}",
        )
    except Exception as e:
        logger.debug(f"overlay graph write skipped for {symbol}: {e}")


async def _resolve_targets(
    cli_symbols: list[str],
) -> list[tuple[str, str]]:
    """Build the (symbol, asset_class) target list for one tick.

    For each registered market that's currently in-session:
      - crypto: use CLI symbols (matches the historical --symbols override)
      - other markets: take the market's universe straight from the adapter

    Out-of-session markets are skipped entirely (no features extracted, no
    config loaded, no log noise).
    """
    targets: list[tuple[str, str]] = []
    for market in all_markets():
        if not market.is_session_open():
            continue
        if market.name == "crypto":
            # Honour the operator's --symbols override; falls back to the
            # adapter's universe when nothing was passed.
            symbols = cli_symbols if cli_symbols else None
            if symbols is None:
                async with session_scope() as db:
                    symbols = await market.universe(db)
            targets.extend((s, market.asset_class) for s in symbols)
        else:
            async with session_scope() as db:
                symbols = await market.universe(db)
            targets.extend((s, market.asset_class) for s in symbols)
    return targets


async def _tick(symbols: list[str]) -> int:
    """Run one decision cycle. Returns number of non-hold predictions persisted.

    Target list comes from `MarketAdapter`s — each in-session market
    contributes its universe (CLI --symbols still overrides crypto).
    Each symbol is paired with its asset_class so the decision layer can
    drop signals that don't apply.
    """
    targets = await _resolve_targets(symbols)

    # Config per asset_class (cached). Pre-load both so we don't re-query
    # every symbol within the same tick.
    cfgs = {ac: await load_agent_config(AGENT_STRATEGY_ID, ac) for ac in {ac for _, ac in targets}}

    # Per-symbol potential score from tradable_symbols — used to scale epsilon
    # exploration (adaptive epsilon: negative-edge symbols aren't explored).
    # Best-effort: missing on empty table (early bootstrap) → neutral for all.
    edge_map: dict[str, float] = {}
    try:
        async with shared_session_scope() as _sess:
            _rows = (await _sess.execute(
                select(TradableSymbol.symbol, TradableSymbol.score)
                .where(TradableSymbol.asset_class.in_(
                    {ac for _, ac in targets}
                ))
            )).all()
            edge_map = {sym: float(sc) for sym, sc in _rows if sc is not None}
    except Exception:
        pass

    persisted = 0
    for symbol, asset_class in targets:
        cfg = cfgs[asset_class]
        # If the strategy has no active strategy_configs row (retired or never
        # promoted), skip emission entirely. The fallback config exists only
        # for bootstrap before the first promotion.
        if cfg.is_fallback:
            logger.debug(
                f"{symbol} [{asset_class}]: skip; no active config for {AGENT_STRATEGY_ID}"
            )
            continue
        try:
            features = await extract_symbol_features(symbol)
            decision = await decide(
                features, cfg, asset_class=asset_class,
                symbol_edge=edge_map.get(symbol),
            )
        except Exception as e:
            logger.exception(f"agent error for {symbol} ({asset_class}): {e}")
            continue

        if decision.side == "hold" or decision.last_price is None:
            logger.debug(f"{symbol} [{asset_class}]: HOLD ({decision.thesis[:80]})")
            continue
        is_exploration = bool(decision.feature_dump.get("is_exploration", False))
        # Exploration trades are intentionally low-confidence probes — they
        # bypass the normal confidence floor (that's the point of exploring).
        if not is_exploration and decision.confidence < Decimal("0.1"):
            logger.debug(
                f"{symbol} [{asset_class}]: skip; conf={decision.confidence:.3f}"
            )
            continue

        now = datetime.now(UTC)
        pred = Prediction(
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
            tp_pct=cfg.tp_pct,
            sl_pct=cfg.sl_pct,
            thesis=decision.thesis,
            context={**decision.feature_dump, "agent_version": cfg.version},
            status="open",
        )
        async with shared_session_scope() as session:
            session.add(pred)
            await session.flush()
            pred_id = str(pred.id)
        persisted += 1
        await _mirror_prediction_to_graph(
            pred_id, symbol, asset_class, decision, cfg.version, is_exploration
        )
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
    # Default empty → _resolve_targets falls back to the crypto market's
    # universe (matrix_shared.markets.crypto.crypto_universe), the single
    # source of truth. Pass --symbols to override for a focused run.
    parser.add_argument("--symbols", nargs="*", default=[])
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

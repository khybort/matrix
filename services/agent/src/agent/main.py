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
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from loguru import logger
from sqlalchemy import select

from matrix_shared import session_scope, shared_session_scope
from matrix_shared.markets import all_markets
from matrix_shared.models import Prediction, TradableSymbol

from agent.config import load_agent_config
from agent.decide import decide_batch
from agent.features import extract_symbol_features

AGENT_STRATEGY_ID = "matrix_agent"

# After the LLM says HOLD for a symbol, don't ask again for a while: with the
# 15 s loop the same 6 symbols were re-decided every tick (one ~30 s, ~20k-token
# CLI call each) and answered HOLD again. Features barely move inside 5 min in
# a low-vol regime; exploration probes and rule decisions are unaffected.
HOLD_COOLDOWN_S = float(__import__("os").environ.get("MATRIX_AGENT_HOLD_COOLDOWN_S", "300"))
# Per-market cap on how many symbols the LLM strategy considers per tick. The
# US universe is the S&P 500 + Nasdaq-100 (503 names): unbounded, the agent
# would page through it 10 symbols per 15 s tick (~600 CLI calls/hour) and
# still trade at most a handful. Best realised edge first; ties keep order.
UNIVERSE_CAP = int(__import__("os").environ.get("MATRIX_AGENT_UNIVERSE_CAP", "60"))
_hold_until: dict[tuple[str, str], float] = {}


def _cap_universe(targets: list[tuple[str, str]], edge_map: dict[str, float],
                  cap: int | None = None) -> list[tuple[str, str]]:
    cap = UNIVERSE_CAP if cap is None else cap
    if cap <= 0:
        return targets
    out: list[tuple[str, str]] = []
    for ac in sorted({ac for _, ac in targets}):
        mine = [t for t in targets if t[1] == ac]
        if len(mine) > cap:
            mine.sort(key=lambda t: edge_map.get(t[0], 0.5), reverse=True)
            logger.debug(f"universe cap [{ac}]: {len(mine)} → {cap} symbols")
            mine = mine[:cap]
        out.extend(mine)
    return out


def _drop_held(targets: list[tuple[str, str]], now_mono: float) -> list[tuple[str, str]]:
    """Filter out (symbol, asset_class) pairs still inside their HOLD cooldown."""
    kept = [t for t in targets if _hold_until.get(t, 0.0) <= now_mono]
    if len(kept) < len(targets):
        logger.debug(f"hold cooldown: skipping {len(targets) - len(kept)} symbol(s) decided HOLD recently")
    return kept


def _mark_hold(symbol: str, asset_class: str, now_mono: float) -> None:
    if HOLD_COOLDOWN_S > 0:
        _hold_until[(symbol, asset_class)] = now_mono + HOLD_COOLDOWN_S

DEFAULT_INTERVAL_S = 15.0


async def _recent_signal_exists(
    symbol: str, asset_class: str, horizon_seconds: int, version: int | None = None
) -> bool:
    """Skip re-emitting while a prior matrix_agent signal is still live.

    Matches deterministic strategy dedup: one open prediction per
    symbol within the horizon window prevents 15s spam filling the wallet.
    """
    since = datetime.now(UTC) - timedelta(seconds=horizon_seconds)
    async with shared_session_scope() as session:
        row = (
            await session.execute(
                select(Prediction.id)
                .where(Prediction.strategy_id == AGENT_STRATEGY_ID)
                .where(Prediction.symbol == symbol)
                .where(Prediction.asset_class == asset_class)
                .where(Prediction.status == "open")
                .where(Prediction.generated_at >= since)
                .where(Prediction.strategy_version == version if version is not None else True)
                .limit(1)
            )
        ).first()
        return row is not None


def _exchange_for(asset_class: str) -> str:
    return {"bist": "BIST", "us": "US"}.get(asset_class, "bybit")


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
        try:
            if market.name == "crypto":
                # Honour the operator's --symbols override; falls back to the
                # adapter's universe when nothing was passed.
                symbols = cli_symbols if cli_symbols else None
                if symbols is None:
                    async with session_scope() as db:
                        symbols = await market.universe(db)
            else:
                async with session_scope() as db:
                    symbols = await market.universe(db)
        except Exception as e:  # noqa: BLE001 — one market's universe must not kill the tick
            # 2026-09-14: the US adapter's `us_symbols` table was not migrated yet;
            # during US hours every agent tick died and crypto stopped deciding.
            logger.warning(f"{market.name}: universe unavailable ({str(e).splitlines()[0][:120]}); skipping market")
            continue
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

    # Filter to symbols with an active config
    active_targets = [
        (sym, ac) for sym, ac in targets
        if not cfgs[ac].is_fallback
    ]
    for sym, ac in targets:
        if cfgs[ac].is_fallback:
            logger.debug(f"{sym} [{ac}]: skip; no active config for {AGENT_STRATEGY_ID}")

    if not active_targets:
        return 0

    # Pre-filter: skip symbols that already have an open prediction within their
    # horizon window. Avoids burning LLM tokens on symbols where the dedup check
    # would reject the signal anyway. Checks run in parallel.
    async def _has_recent(sym: str, ac: str) -> bool:
        try:
            return await _recent_signal_exists(sym, ac, cfgs[ac].horizon_seconds, cfgs[ac].version)
        except Exception:
            return False

    dedup_flags = await asyncio.gather(*[_has_recent(s, ac) for s, ac in active_targets])
    fresh_targets = [t for t, dup in zip(active_targets, dedup_flags) if not dup]
    for (sym, ac), dup in zip(active_targets, dedup_flags):
        if dup:
            logger.debug(f"{sym} [{ac}]: pre-dedup skip (open signal within horizon)")

    if not fresh_targets:
        return 0

    # Backpressure: an LLM call for a symbol whose prediction will expire
    # unfilled is pure cost (1,441 expired vs 36 traded on 2026-09-13). Keep,
    # per market, only as many targets as the open backlog has room for,
    # best realised edge first.
    fresh_targets = _cap_universe(fresh_targets, edge_map)
    fresh_targets = _drop_held(fresh_targets, time.monotonic())
    fresh_targets = await _trim_to_room(fresh_targets, edge_map)
    if not fresh_targets:
        return 0

    # Parallel feature extraction — only for symbols without a recent signal
    async def _safe_features(sym: str, ac: str):
        try:
            return sym, ac, await extract_symbol_features(sym, ac)
        except Exception as e:
            logger.exception(f"feature extraction failed for {sym} ({ac}): {e}")
            return sym, ac, None

    feat_results = await asyncio.gather(*[_safe_features(s, ac) for s, ac in fresh_targets])
    # No price = nothing to trade and nothing worth an LLM call (a stale or
    # halted symbol); the decision would be HOLD on an empty prompt anyway.
    valid = [(sym, ac, feat) for sym, ac, feat in feat_results if feat is not None and feat.last_price is not None]
    for sym, ac, feat in feat_results:
        if feat is not None and feat.last_price is None:
            logger.debug(f"{sym} [{ac}]: no fresh price; skip")

    if not valid:
        return 0

    # One batch LLM call for all symbols → concurrent lessons
    batch_items = [
        (feat, cfgs[ac], ac, edge_map.get(sym))
        for sym, ac, feat in valid
    ]
    decisions = await decide_batch(batch_items, strategy_id=AGENT_STRATEGY_ID)

    persisted = 0
    for (symbol, asset_class, _feat), decision in zip(valid, decisions):
        cfg = cfgs[asset_class]

        if decision.side == "hold" or decision.last_price is None:
            logger.debug(f"{symbol} [{asset_class}]: HOLD ({decision.thesis[:80]})")
            if not decision.feature_dump.get("is_exploration"):
                _mark_hold(symbol, asset_class, time.monotonic())
            continue
        is_exploration = bool(decision.feature_dump.get("is_exploration", False))
        # Exploration trades are intentionally low-confidence probes — they
        # bypass the normal confidence floor (that's the point of exploring).
        if not is_exploration and decision.confidence < Decimal("0.1"):
            logger.debug(
                f"{symbol} [{asset_class}]: skip; conf={decision.confidence:.3f}"
            )
            continue

        # Dedup guard: belt-and-suspenders in case a concurrent tick slipped through
        if await _recent_signal_exists(symbol, asset_class, cfg.horizon_seconds, cfg.version):
            logger.debug(f"{symbol} [{asset_class}]: dedup skip (concurrent race)")
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
            context={**decision.feature_dump, "agent_version": cfg.version, "method": decision.method},
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

    # Challenger pass: a `shadow` config decides rule-only on the same features
    # and books into the shadow wallet (context.is_shadow). No LLM spend.
    try:
        persisted += await _shadow_pass(valid)
    except Exception as e:
        logger.warning(f"shadow pass failed (non-fatal): {e}")

    return persisted


async def _trim_to_room(targets: list[tuple[str, str]], edge_map: dict[str, float]) -> list[tuple[str, str]]:
    from matrix_shared.backpressure import room

    out: list[tuple[str, str]] = []
    for ac in sorted({ac for _, ac in targets}):
        mine = [t for t in targets if t[1] == ac]
        r = await room(AGENT_STRATEGY_ID, ac)
        if r >= len(mine):
            out.extend(mine)
            continue
        mine.sort(key=lambda t: edge_map.get(t[0], 0.5), reverse=True)
        out.extend(mine[:r])
        logger.info(f"backpressure [{ac}]: open backlog full — deciding {r}/{len(mine)} symbols this tick")
    return out


async def _shadow_pass(valid: list) -> int:
    from agent.config import load_shadow_config
    from agent.decide import rule_decide

    shadow_cfgs = {ac: await load_shadow_config(AGENT_STRATEGY_ID, ac) for ac in {ac for _, ac, _ in valid}}
    if not any(shadow_cfgs.values()):
        return 0
    persisted = 0
    for symbol, asset_class, feat in valid:
        sh = shadow_cfgs.get(asset_class)
        if sh is None:
            continue
        d = rule_decide(feat, sh.weights, sh.signal_threshold, asset_class=asset_class)
        if d.side == "hold" or d.last_price is None or d.confidence < Decimal("0.1"):
            continue
        if await _recent_signal_exists(symbol, asset_class, sh.horizon_seconds, sh.version):
            continue
        now = datetime.now(UTC)
        pred = Prediction(
            strategy_id=AGENT_STRATEGY_ID,
            strategy_version=sh.version,
            generated_at=now,
            symbol=symbol,
            exchange=_exchange_for(asset_class),
            asset_class=asset_class,
            side=d.side,
            confidence=d.confidence,
            horizon_seconds=sh.horizon_seconds,
            close_by=now + timedelta(seconds=sh.horizon_seconds),
            entry_price_ref=d.last_price,
            tp_pct=sh.tp_pct,
            sl_pct=sh.sl_pct,
            thesis=d.thesis,
            context={**d.feature_dump, "agent_version": sh.version, "method": "rule+shadow", "is_shadow": True},
            status="open",
        )
        async with shared_session_scope() as session:
            session.add(pred)
        persisted += 1
        logger.info(f"{symbol} [{asset_class}] shadow v{sh.version}: {d.side.upper()} conf={d.confidence:.3f}")
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

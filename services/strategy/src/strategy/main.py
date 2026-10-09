"""Strategy generator loop.

Each tick the dispatcher walks every registered `MarketAdapter` and, while
that market's session is open, runs every strategy class registered for
it. Drafts go to predictions table; paper-trade engine consumes from there.

Strategies declare their market via the `market` ClassVar. They no longer
self-gate on session hours — `MarketAdapter.is_session_open()` does that.

Usage:
    uv run python -m strategy.main                    # default 30s loop
    uv run python -m strategy.main --interval 10      # faster loop
    uv run python -m strategy.main --once             # one cycle and exit
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
import time

from loguru import logger

from matrix_shared import shared_session_scope
from matrix_shared.markets import MarketAdapter, all_markets
from matrix_shared.markets.crypto import carry_watchlist_async, crypto_universe_async
from matrix_shared.models import StrategyConfig
from sqlalchemy import select

from strategy.base import PredictionDraft
from strategy.freshness import apply_freshness_guard
from strategy.lessons import filter_drafts
from strategy.modules import STRATEGIES_BY_MARKET
from strategy.params import instantiate
from strategy.persist import persist_drafts

DEFAULT_INTERVAL_S = 30.0

# strategy_configs DB gate + params — re-read every this many seconds so an
# operator (or the reflection/labs apply path) can retire a strategy or change
# its params at runtime without restarting the container.
_CONFIG_TTL_S = 60.0
_configs_cache: dict[tuple[str, str], tuple[int, dict]] | None = None
_shadow_cache: dict[tuple[str, str], tuple[int, dict]] = {}
_configs_ts: float = 0.0


async def _active_configs() -> dict[tuple[str, str], tuple[int, dict]]:
    """(strategy_id, asset_class) → (version, params) for every ACTIVE row.

    Empty dict when the table has NO active rows at all — bootstrap state,
    every registered strategy runs with module defaults at version 1.
    """
    global _configs_cache, _shadow_cache, _configs_ts
    now = time.monotonic()
    if _configs_cache is not None and now - _configs_ts < _CONFIG_TTL_S:
        return _configs_cache

    async with shared_session_scope() as session:
        rows = (await session.execute(
            select(StrategyConfig.strategy_id, StrategyConfig.asset_class,
                   StrategyConfig.version, StrategyConfig.params, StrategyConfig.status)
            .where(StrategyConfig.status.in_(("active", "shadow")))
            .order_by(StrategyConfig.version.desc())
        )).all()

    cfgs: dict[tuple[str, str], tuple[int, dict]] = {}
    shadows: dict[tuple[str, str], tuple[int, dict]] = {}
    for sid, ac, ver, params, status in rows:
        target = cfgs if status == "active" else shadows
        target.setdefault((sid, ac), (int(ver), dict(params or {})))
    _configs_cache = cfgs
    _shadow_cache = shadows
    _configs_ts = now
    return cfgs


def _instantiate_for_market(
    market: MarketAdapter,
    symbols: list[str] | None = None,
    configs: dict[tuple[str, str], tuple[int, dict]] | None = None,
    shadows: dict[tuple[str, str], tuple[int, dict]] | None = None,
) -> list:
    """Build strategy instances for `market`, bound to their active config.

    `symbols`, when given, is passed to every strategy ctor so the whole
    market shares one dynamically-resolved symbol set (the active universe).
    `configs` (from `_active_configs`) supplies version + tuned params; a
    strategy with no active row is skipped unless `configs` is empty
    (bootstrap mode).

    A KeyError here means a market was registered as a MarketAdapter but has
    no `modules/<market>/__init__.py` exporting STRATEGIES — surface loudly.
    """
    if market.name not in STRATEGIES_BY_MARKET:
        logger.warning(
            f"market {market.name!r} has no registered strategies "
            f"(STRATEGIES_BY_MARKET keys: {sorted(STRATEGIES_BY_MARKET)})"
        )
        return []
    configs = configs or {}
    shadows = shadows if shadows is not None else _shadow_cache
    out = []
    for cls in STRATEGIES_BY_MARKET[market.name]:
        key = (cls.id, market.asset_class)
        cfg = configs.get(key)
        sh = shadows.get(key)
        if cfg is None and not configs:
            out.append(instantiate(cls, symbols=symbols))
            continue
        if cfg is None and sh is None:
            logger.debug(f"strategy {cls.id}/{market.asset_class}: no active or shadow config row; skip")
            continue
        if cfg is not None:
            version, params = cfg
            try:
                out.append(instantiate(cls, symbols=symbols, version=version, params=params))
            except Exception as e:
                logger.exception(
                    f"strategy {cls.id}/{market.asset_class} v{version}: params rejected "
                    f"({e}); falling back to defaults"
                )
                out.append(instantiate(cls, symbols=symbols, version=version))
        # Challenger: a `shadow` config runs side by side on the same data; its
        # drafts are tagged is_shadow so the paper engine books them in the
        # shadow wallet and reflection.efficacy can compare it to the champion.
        # A strategy with ONLY a shadow row is a new signal earning its first
        # evidence: it trades the shadow wallet alone, and with no champion to
        # beat, efficacy never cuts it over — promotion stays with the evidence.
        if sh is not None:
            sh_version, sh_params = sh
            try:
                challenger = instantiate(cls, symbols=symbols, version=sh_version, params=sh_params)
                challenger.matrix_is_shadow = True
                out.append(challenger)
            except Exception as e:
                logger.exception(
                    f"strategy {cls.id}/{market.asset_class} shadow v{sh_version}: params rejected ({e})"
                )
    return out


async def _apply_vol_barriers(drafts: list) -> int:
    """Rewrite each draft's tp/sl to m·σ_h for its symbol (matrix_shared.barriers).

    Fixed-percentage barriers mean different things in different regimes: the
    2026-09-20 barrier study found take-profits sitting 8.8σ (grid) and 6.8σ
    (matrix_agent) out, unreachable inside their own horizon, which is why 57%
    of exits were time exits at minus the round trip. Strategies that set no
    barriers, and symbols with unknown volatility, are left untouched.
    """
    from matrix_shared.barriers import ENABLED, vol_scaled_barriers

    if not ENABLED:
        return 0
    rescaled = 0
    for d in drafts:
        if d.tp_pct is None and d.sl_pct is None:
            continue  # carry / delta-neutral legs have no price barrier
        try:
            scaled = await vol_scaled_barriers(
                d.symbol, d.asset_class, d.horizon_seconds, tp_pct=d.tp_pct, sl_pct=d.sl_pct
            )
        except Exception as e:  # noqa: BLE001 — never block a tick on this
            logger.debug(f"vol barrier skipped for {d.symbol}: {e}")
            continue
        if scaled is None:
            continue
        tp, sl = scaled
        d.context = {
            **(d.context or {}),
            "barrier": {"mode": "vol_scaled", "tp_pct": str(tp), "sl_pct": str(sl),
                        "was_tp_pct": str(d.tp_pct), "was_sl_pct": str(d.sl_pct)},
        }
        d.tp_pct, d.sl_pct = tp, sl
        rescaled += 1
    if rescaled:
        logger.debug(f"vol-scaled barriers applied to {rescaled}/{len(drafts)} draft(s)")
    return rescaled


async def _tick() -> int:
    configs = await _active_configs()
    # Resolve the crypto active universe once per tick (async — the sync path
    # returns _DEFAULT_UNIVERSE under a running loop), then feed it to every
    # crypto strategy so they analyze the same set ingestion streams + the agent
    # trades. Other markets resolve their own universe internally.
    crypto_symbols = await crypto_universe_async()
    # Carry coins stream via ingestion's watchlist but are not traded directionally:
    # only the carry module sees them, so no directional strategy picks a coin
    # chosen for its funding.
    carry_symbols = crypto_symbols + [s for s in await carry_watchlist_async() if s not in crypto_symbols]
    drafts: list[PredictionDraft] = []
    for market in all_markets():
        if not market.is_session_open():
            logger.debug(f"market {market.name} session closed; skip")
            continue
        market_symbols = crypto_symbols if market.name == "crypto" else None
        for strat in _instantiate_for_market(market, market_symbols, configs):
            if market.name == "crypto" and strat.id == "neg_funding_carry":
                strat.symbols = carry_symbols
            try:
                ds = await strat.generate()
                if getattr(strat, "matrix_is_shadow", False):
                    for d in ds:
                        d.context = {**(d.context or {}), "is_shadow": True}
                drafts.extend(ds)
            except Exception as e:
                logger.exception(f"strategy {strat.id} ({market.name}) failed: {e}")

    # Frozen feed (outage) → frozen prices: stand down rather than signal on them.
    drafts = await apply_freshness_guard(drafts)

    # Data-driven filter: drop candidates that match an active 'avoid' lesson.
    # Keeps strategy modules symbol-agnostic; lessons are produced by the
    # lessons feeder and can be added/removed without code changes.
    # Regime tag (per market) so lessons/efficacy can be keyed by regime.
    try:
        from matrix_shared.regime import current_regime

        regimes = {ac: (await current_regime(ac)).key for ac in {d.asset_class for d in drafts}}
        for d in drafts:
            d.context = {**(d.context or {}), "regime": regimes.get(d.asset_class, "unknown")}
    except Exception as e:  # noqa: BLE001
        logger.debug(f"regime tag skipped: {e}")

    await _apply_vol_barriers(drafts)
    drafts = await filter_drafts(drafts)
    return await persist_drafts(drafts)


async def run(interval_s: float) -> None:
    stop = asyncio.Event()

    def _handle_signal(*_: object) -> None:
        logger.info("shutdown signal received")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal)

    while not stop.is_set():
        try:
            n = await _tick()
            logger.info(f"tick: {n} new predictions")
        except Exception as e:
            logger.exception(f"tick error: {e}")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
        except TimeoutError:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Matrix strategy generator")
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL_S,
        help=f"Generator loop interval seconds (default {DEFAULT_INTERVAL_S})",
    )
    parser.add_argument(
        "--once", action="store_true", help="Run one tick and exit"
    )
    args = parser.parse_args()

    logger.remove()
    logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level: <5} | {message}")
    logger.info(
        f"strategy start: interval={args.interval}s once={args.once} "
        f"markets={[m.name for m in all_markets()]}"
    )

    if args.once:
        asyncio.run(_tick())
    else:
        asyncio.run(run(args.interval))


if __name__ == "__main__":
    main()

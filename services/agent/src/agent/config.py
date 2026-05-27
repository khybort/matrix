"""Live agent config loader.

The agent's runtime parameters (weights, signal_threshold, horizon_seconds)
live in the `strategy_configs` table — they used to be hardcoded. Promotion
of a lab-discovered genome replaces this row, so we re-read it every tick
(or every TTL seconds with a small cache).

Module-level cache: cheap, single-process, fine for our loop intervals.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal

from loguru import logger
from matrix_shared import shared_session_scope
from matrix_shared.models import StrategyConfig
from sqlalchemy import select

CACHE_TTL_S = 10.0  # tick interval is ~15s, so this triggers fresh read each tick


@dataclass(slots=True)
class AgentConfig:
    version: int
    weights: dict[str, Decimal]
    signal_threshold: Decimal
    horizon_seconds: int
    # Epsilon-greedy exploration rate (paper-trade only): fraction of holds
    # converted to low-confidence exploratory trades to feed the learning loop.
    explore_epsilon: float = 0.15
    # True when no active strategy_configs row exists for this (strategy_id,
    # asset_class). Callers should treat this as "strategy retired — skip
    # emitting predictions." The fallback is a bootstrap aid, not a
    # default-on state for retired strategies.
    is_fallback: bool = False


# Fallback used during bootstrap before any strategy_configs row exists.
# When a strategy is RETIRED (had a row, now status != 'active'), the
# agent main loop checks `is_fallback` and skips that strategy entirely.
FALLBACK = AgentConfig(
    version=1,
    weights={
        "trade_flow": Decimal("0.35"),
        "funding": Decimal("0.20"),
        "oi_delta": Decimal("0.20"),
        "ob_imbalance": Decimal("0.15"),
        "news": Decimal("0.10"),
    },
    signal_threshold=Decimal("0.18"),
    horizon_seconds=120,
    explore_epsilon=0.15,
    is_fallback=True,
)


_cache: dict[tuple[str, str], tuple[float, AgentConfig]] = {}


async def load_agent_config(
    strategy_id: str = "matrix_agent", asset_class: str = "crypto"
) -> AgentConfig:
    now = time.monotonic()
    key = (strategy_id, asset_class)
    cached = _cache.get(key)
    if cached and now - cached[0] < CACHE_TTL_S:
        return cached[1]

    async with shared_session_scope() as session:
        stmt = (
            select(StrategyConfig)
            .where(StrategyConfig.strategy_id == strategy_id)
            .where(StrategyConfig.asset_class == asset_class)
            .where(StrategyConfig.status == "active")
            .order_by(StrategyConfig.version.desc())
            .limit(1)
        )
        row = (await session.execute(stmt)).scalar_one_or_none()

    if row is None:
        logger.debug(
            f"no active StrategyConfig for {strategy_id}/{asset_class}, using fallback"
        )
        _cache[key] = (now, FALLBACK)
        return FALLBACK

    params = row.params or {}
    raw_w = params.get("weights", {}) or {}
    weights = {
        k: Decimal(str(v))
        for k, v in raw_w.items()
        if k in {"trade_flow", "funding", "oi_delta", "ob_imbalance", "news"}
    }
    # Backfill any missing feature with a small weight; let it normalize later
    for f in ("trade_flow", "funding", "oi_delta", "ob_imbalance", "news"):
        weights.setdefault(f, Decimal("0.05"))

    cfg = AgentConfig(
        version=row.version,
        weights=weights,
        signal_threshold=Decimal(str(params.get("signal_threshold", "0.18"))),
        horizon_seconds=int(params.get("horizon_seconds", 120)),
        explore_epsilon=float(params.get("explore_epsilon", 0.15)),
    )
    _cache[key] = (now, cfg)
    return cfg


def invalidate_cache(strategy_id: str | None = None) -> None:
    """Call after apply() to force a refresh on next tick."""
    if strategy_id is None:
        _cache.clear()
    else:
        for k in list(_cache.keys()):
            if k[0] == strategy_id:
                _cache.pop(k, None)

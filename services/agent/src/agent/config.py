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
from sqlalchemy import select

from matrix_shared import session_scope
from matrix_shared.models import StrategyConfig

CACHE_TTL_S = 10.0  # tick interval is ~15s, so this triggers fresh read each tick


@dataclass(slots=True)
class AgentConfig:
    version: int
    weights: dict[str, Decimal]
    signal_threshold: Decimal
    horizon_seconds: int


# Fallback used when the DB has nothing (shouldn't happen post-bootstrap).
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
)


_cache: dict[str, tuple[float, AgentConfig]] = {}


async def load_agent_config(strategy_id: str = "matrix_agent") -> AgentConfig:
    now = time.monotonic()
    cached = _cache.get(strategy_id)
    if cached and now - cached[0] < CACHE_TTL_S:
        return cached[1]

    async with session_scope() as session:
        stmt = (
            select(StrategyConfig)
            .where(StrategyConfig.strategy_id == strategy_id)
            .where(StrategyConfig.status == "active")
            .order_by(StrategyConfig.version.desc())
            .limit(1)
        )
        row = (await session.execute(stmt)).scalar_one_or_none()

    if row is None:
        logger.warning(f"no active StrategyConfig for {strategy_id}, using fallback")
        _cache[strategy_id] = (now, FALLBACK)
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
    )
    _cache[strategy_id] = (now, cfg)
    return cfg


def invalidate_cache(strategy_id: str | None = None) -> None:
    """Call after apply() to force a refresh on next tick."""
    if strategy_id is None:
        _cache.clear()
    else:
        _cache.pop(strategy_id, None)

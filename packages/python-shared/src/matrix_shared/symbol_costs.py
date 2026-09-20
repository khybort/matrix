"""Measured per-symbol execution cost, replacing one flat slippage assumption.

The EV floor, the barrier study and every "does this edge pay for itself"
judgement are all denominated in the round-trip cost. Until now that was a
single constant (2 bps slippage on top of the taker fee) applied to every
symbol. Measured on 2026-09-20 from this system's own order books, the median
spread across the traded universe ranges 1.16 bps (UNIUSDT) to 5.95 bps
(BRUSDT) — a 5× spread. One constant is therefore simultaneously too generous
for the liquid names and far too optimistic for the illiquid ones, and it
distorts the gate in both directions: it lets marginal altcoin trades through
and blocks good majors trades.

Crossing the spread costs half of it, so the slippage allowance per side is
`half_spread × IMPACT_MULT`, floored at MIN_SLIPPAGE_BPS so a momentarily tight
book cannot make a symbol look free. Measurements are cached as JSON on the
shared volume; a symbol we have never measured falls back to the flat constant,
so this can only refine the estimate, never break the gate.
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger
from sqlalchemy import text

from matrix_shared.db import local_session_scope

COSTS_PATH = Path(os.environ.get("MATRIX_MODEL_DIR") or
                  os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"),
                               "matrix_models")) / "symbol_costs.json"
MIN_SLIPPAGE_BPS = float(os.environ.get("MATRIX_MIN_SLIPPAGE_BPS", "0.5"))
MAX_SLIPPAGE_BPS = float(os.environ.get("MATRIX_MAX_SLIPPAGE_BPS", "25"))
IMPACT_MULT = float(os.environ.get("MATRIX_SPREAD_IMPACT_MULT", "1.0"))
MIN_SNAPSHOTS = int(os.environ.get("MATRIX_COST_MIN_SNAPSHOTS", "50"))
CACHE_TTL_S = float(os.environ.get("MATRIX_COST_CACHE_TTL_S", "300"))

_cache: tuple[float, dict[str, float]] | None = None


def slippage_from_spread(median_spread_bps: float) -> float:
    """Per-side slippage allowance from a median quoted spread."""
    half = max(0.0, median_spread_bps) / 2.0
    return min(MAX_SLIPPAGE_BPS, max(MIN_SLIPPAGE_BPS, half * IMPACT_MULT))


async def measure_spreads(hours: float = 6.0) -> dict[str, float]:
    """Median top-of-book spread in bps per symbol, from our own snapshots."""
    sql = text(
        "SELECT symbol, count(*) AS n, "
        "  percentile_cont(0.5) WITHIN GROUP (ORDER BY "
        "    ((asks->0->>0)::numeric - (bids->0->>0)::numeric) "
        "    / nullif(((asks->0->>0)::numeric + (bids->0->>0)::numeric)/2, 0) * 10000) AS med "
        "FROM market_orderbook_snapshots "
        "WHERE snapshot_ts > now() - make_interval(secs => :secs) "
        "  AND json_array_length(bids) > 0 AND json_array_length(asks) > 0 "
        "GROUP BY 1 HAVING count(*) >= :min_n"
    )
    out: dict[str, float] = {}
    async with local_session_scope() as s:
        for sym, _n, med in (await s.execute(sql, {"secs": hours * 3600, "min_n": MIN_SNAPSHOTS})).all():
            if med is not None and float(med) > 0:
                out[str(sym)] = float(med)
    return out


async def refresh(hours: float = 6.0) -> int:
    """Re-measure and persist. Returns how many symbols were written."""
    spreads = await measure_spreads(hours)
    if not spreads:
        logger.info("symbol costs: no order-book data in window; keeping previous file")
        return 0
    payload = {
        "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "hours": hours,
        "slippage_bps": {sym: round(slippage_from_spread(v), 3) for sym, v in spreads.items()},
        "median_spread_bps": {sym: round(v, 3) for sym, v in spreads.items()},
    }
    try:
        COSTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        COSTS_PATH.write_text(json.dumps(payload, indent=1))
    except OSError as e:
        logger.warning(f"symbol costs not written: {e}")
        return 0
    global _cache
    _cache = None
    logger.info(f"symbol costs refreshed for {len(spreads)} symbol(s)")
    return len(spreads)


def _load() -> dict[str, float]:
    global _cache
    now = time.monotonic()
    if _cache and now - _cache[0] < CACHE_TTL_S:
        return _cache[1]
    data: dict[str, float] = {}
    try:
        if COSTS_PATH.exists():
            data = {k: float(v) for k, v in json.loads(COSTS_PATH.read_text())["slippage_bps"].items()}
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        logger.debug(f"symbol costs unreadable ({e}); using the flat allowance")
    _cache = (now, data)
    return data


def slippage_bps_for(symbol: str | None) -> float | None:
    """Measured per-side slippage for this symbol, or None to use the flat one."""
    if not symbol:
        return None
    return _load().get(symbol)


def clear_cache() -> None:
    global _cache
    _cache = None

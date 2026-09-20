"""Volatility-scaled take-profit / stop-loss (López de Prado's triple barrier).

A barrier written as a fixed percentage means something different in every
regime. Measured on this system 2026-09-20 (`make barrier-report`), the current
take-profits sit this far out in units of horizon volatility σ_h:

    grid 8.8σ · matrix_agent 6.8σ · bist_volume_breakout 5.6σ · dca 1.3σ

A barrier at 8.8σ is not a barrier — the trade cannot reach it inside its own
horizon, so it always exits at the time barrier having paid the round trip.
That is exactly the 57% horizon-exit share in docs/wiki/pnl-reality.md.

Scaling instead by m·σ_h keeps the label meaningful across regimes. Replaying
the same signals with m-scaled barriers took `oi_breakout` from −6.7 to +14.1
net bps and `momentum_xs` from +15.7 to +26.9.

`MATRIX_VOL_BARRIERS=0` disables the whole mechanism and restores the
strategies' own fixed percentages.
"""

from __future__ import annotations

import math
import os
import time
from decimal import Decimal

from loguru import logger
from sqlalchemy import text

from matrix_shared.db import local_session_scope

ENABLED = os.environ.get("MATRIX_VOL_BARRIERS", "1").strip().lower() not in ("0", "false")
DEFAULT_M = float(os.environ.get("MATRIX_BARRIER_M", "1.5"))
LOOKBACK_BARS = int(os.environ.get("MATRIX_BARRIER_LOOKBACK_BARS", "60"))
MIN_PCT = float(os.environ.get("MATRIX_BARRIER_MIN_PCT", "0.0015"))  # never inside the spread
MAX_PCT = float(os.environ.get("MATRIX_BARRIER_MAX_PCT", "0.03"))
_TTL_S = float(os.environ.get("MATRIX_BARRIER_VOL_TTL_S", "60"))

_vol_cache: dict[tuple[str, str], tuple[float, float]] = {}


def clear_cache() -> None:
    _vol_cache.clear()


def scale(sigma_per_bar: float, horizon_seconds: int | float, m: float = DEFAULT_M) -> float | None:
    """Barrier distance as a fraction of price: m · σ_bar · √(horizon in bars),
    clamped to [MIN_PCT, MAX_PCT]. None when volatility is unknown."""
    if sigma_per_bar <= 0 or not math.isfinite(sigma_per_bar):
        return None
    bars = max(1.0, float(horizon_seconds or 0) / 60.0)
    dist = m * sigma_per_bar * math.sqrt(bars)
    if not math.isfinite(dist) or dist <= 0:
        return None
    return min(MAX_PCT, max(MIN_PCT, dist))


async def per_bar_vol(symbol: str, asset_class: str) -> float:
    """Std-dev of 1m log returns over LOOKBACK_BARS, cached for _TTL_S."""
    key = (symbol, asset_class)
    hit = _vol_cache.get(key)
    now = time.monotonic()
    if hit and now - hit[0] < _TTL_S:
        return hit[1]
    sigma = 0.0
    try:
        async with local_session_scope() as s:
            rows = (await s.execute(text(
                "SELECT close FROM (SELECT ts, close FROM market_bars "
                "WHERE symbol = :sym AND asset_class = :ac AND interval = '1m' "
                "ORDER BY ts DESC LIMIT :lim) q ORDER BY ts"
            ), {"sym": symbol, "ac": asset_class, "lim": LOOKBACK_BARS})).all()
        closes = [float(r[0]) for r in rows if r[0] and float(r[0]) > 0]
        if len(closes) >= 12:
            rets = [math.log(b / a) for a, b in zip(closes[:-1], closes[1:])]
            mean = sum(rets) / len(rets)
            var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
            sigma = math.sqrt(var)
    except Exception as e:  # noqa: BLE001 — advisory; fall back to the fixed barrier
        logger.debug(f"barrier vol probe failed for {symbol}/{asset_class}: {e}")
    _vol_cache[key] = (now, sigma)
    return sigma


def preserve_ratio(dist: float, tp0: Decimal | float | None, sl0: Decimal | float | None
                   ) -> tuple[Decimal, Decimal]:
    """Anchor the stop at `dist` (= m·σ_h) and keep the strategy's own payoff
    ratio for the target.

    Volatility sets the *scale* — a stop must sit outside the noise band — but
    the tp:sl ratio is the strategy's thesis and not ours to flatten. Making
    both legs equal also breaks the EV floor: a symmetric bracket only clears
    the round trip above a 60% hit rate, so on 2026-09-20 it silently stopped
    the whole book from opening. Ratio is clamped to [0.5, 4] so a degenerate
    config cannot produce an unreachable target again.
    """
    try:
        r = float(tp0) / float(sl0) if tp0 and sl0 and float(sl0) > 0 else 2.0
    except (TypeError, ValueError, ZeroDivisionError):
        r = 2.0
    r = max(0.5, min(4.0, r))
    sl = min(MAX_PCT, max(MIN_PCT, dist))
    tp = min(MAX_PCT, max(MIN_PCT, dist * r))
    return Decimal(str(round(tp, 6))), Decimal(str(round(sl, 6)))


async def vol_scaled_barriers(
    symbol: str, asset_class: str, horizon_seconds: int | float, *, m: float = DEFAULT_M,
    tp_pct: Decimal | float | None = None, sl_pct: Decimal | float | None = None,
) -> tuple[Decimal, Decimal] | None:
    """(tp_pct, sl_pct) scaled to this symbol's current volatility, keeping the
    caller's payoff ratio. None when volatility is unknown and the caller
    should keep its own barriers."""
    if not ENABLED:
        return None
    dist = scale(await per_bar_vol(symbol, asset_class), horizon_seconds, m)
    if dist is None:
        return None
    return preserve_ratio(dist, tp_pct, sl_pct)

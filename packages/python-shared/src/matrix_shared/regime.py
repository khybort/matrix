"""Market regime memory (docs/AUTONOMY_PLAN.md P2.3).

Every lesson, edge and weight was regime-blind: a pattern learned in a
trending week was applied unchanged in a chopping one. This module gives the
system one coarse, deterministic label per market that every prediction is
tagged with (`context.regime`), so lessons can be bucketed by regime and the
LLM/rule blend can see it.

Regime key = "<vol>/<trend>/<funding>" from a reference symbol's 1h bars:
    vol      low | mid | high    realised 24h vol vs its 7-day median (×0.7 / ×1.4)
    trend    down | flat | up    24h return vs ±TREND_PCT (default 1.5%)
    funding  neg | neutral | pos latest 8h funding rate vs ±0.01%
Unknown when data is missing (never blocks a tick). Cached per process for
REGIME_TTL_S (default 300s).
"""

from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from loguru import logger
from sqlalchemy import text

from matrix_shared.db import local_session_scope

REFERENCE_SYMBOL = {"crypto": os.environ.get("MATRIX_REGIME_REF_CRYPTO", "BTCUSDT"),
                    "bist": os.environ.get("MATRIX_REGIME_REF_BIST", "THYAO.IS"),
                    "us": os.environ.get("MATRIX_REGIME_REF_US", "SPY")}
# Tried in order when the primary reference has no 1h history (the US universe
# is S&P 500 + Nasdaq-100 constituents, so SPY/QQQ themselves are not ingested).
REFERENCE_FALLBACKS = {"us": ["QQQ", "AAPL", "MSFT", "NVDA"], "bist": ["GARAN.IS", "AKBNK.IS"], "crypto": ["ETHUSDT"]}


async def _closes_1h(session, symbol: str, asset_class: str, since) -> list[float]:
    rows = (await session.execute(text(
        "SELECT close FROM market_bars WHERE symbol = :s AND asset_class = :ac "
        "AND interval = '1h' AND ts >= :since ORDER BY ts"
    ), {"s": symbol, "ac": asset_class, "since": since})).all()
    return [float(r[0]) for r in rows]
TREND_PCT = float(os.environ.get("MATRIX_REGIME_TREND_PCT", "0.015"))
FUNDING_NEUTRAL = float(os.environ.get("MATRIX_REGIME_FUNDING_NEUTRAL", "0.0001"))
REGIME_TTL_S = float(os.environ.get("MATRIX_REGIME_TTL_S", "300"))

UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Regime:
    vol: str
    trend: str
    funding: str
    ret_24h: float | None = None
    vol_24h: float | None = None
    vol_ratio: float | None = None

    @property
    def key(self) -> str:
        return f"{self.vol}/{self.trend}/{self.funding}"

    def as_dict(self) -> dict:
        return {"key": self.key, "vol": self.vol, "trend": self.trend, "funding": self.funding,
                "ret_24h": self.ret_24h, "vol_24h": self.vol_24h, "vol_ratio": self.vol_ratio}


UNKNOWN_REGIME = Regime(UNKNOWN, UNKNOWN, UNKNOWN)


def classify(closes_1h: list[float], funding_rate: float | None) -> Regime:
    """Pure classifier over up to 7 days of hourly closes (oldest → newest)."""
    if len(closes_1h) < 26:
        return UNKNOWN_REGIME
    rets = [math.log(b / a) for a, b in zip(closes_1h[:-1], closes_1h[1:]) if a > 0 and b > 0]
    if len(rets) < 25:
        return UNKNOWN_REGIME

    def _std(xs: list[float]) -> float:
        if len(xs) < 2:
            return 0.0
        m = sum(xs) / len(xs)
        return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))

    vol_24h = _std(rets[-24:])
    windows = [rets[i:i + 24] for i in range(0, len(rets) - 24 + 1, 24)]
    baseline = sorted(_std(w) for w in windows if len(w) == 24)
    median = baseline[len(baseline) // 2] if baseline else vol_24h
    ratio = (vol_24h / median) if median > 0 else 1.0
    vol = "low" if ratio < 0.7 else "high" if ratio > 1.4 else "mid"
    ret_24h = closes_1h[-1] / closes_1h[-25] - 1.0 if closes_1h[-25] > 0 else 0.0
    trend = "up" if ret_24h > TREND_PCT else "down" if ret_24h < -TREND_PCT else "flat"
    if funding_rate is None:
        funding = UNKNOWN
    else:
        funding = "pos" if funding_rate > FUNDING_NEUTRAL else "neg" if funding_rate < -FUNDING_NEUTRAL else "neutral"
    return Regime(vol, trend, funding, round(ret_24h, 5), round(vol_24h, 5), round(ratio, 3))


_cache: dict[str, tuple[float, Regime]] = {}


async def current_regime(asset_class: str = "crypto", *, symbol: str | None = None) -> Regime:
    """Regime for a market from its reference symbol (cached REGIME_TTL_S)."""
    key = f"{asset_class}:{symbol or ''}"
    hit = _cache.get(key)
    now = time.monotonic()
    if hit and now - hit[0] < REGIME_TTL_S:
        return hit[1]
    ref = symbol or REFERENCE_SYMBOL.get(asset_class, "BTCUSDT")
    regime = UNKNOWN_REGIME
    try:
        since = datetime.now(UTC) - timedelta(days=7, hours=2)
        async with local_session_scope() as session:
            closes = await _closes_1h(session, ref, asset_class, since)
            if len(closes) < 26 and symbol is None:
                for alt in REFERENCE_FALLBACKS.get(asset_class, []):
                    closes = await _closes_1h(session, alt, asset_class, since)
                    if len(closes) >= 26:
                        ref = alt
                        break
            funding = None
            if asset_class == "crypto":
                fr = (await session.execute(text(
                    "SELECT funding_rate FROM market_ticker_snapshots WHERE symbol = :s "
                    "AND funding_rate IS NOT NULL ORDER BY snapshot_ts DESC LIMIT 1"
                ), {"s": ref})).scalar()
                funding = float(fr) if fr is not None else None
        regime = classify(closes, funding)
    except Exception as e:  # noqa: BLE001 — regime is advisory, never blocks a tick
        logger.debug(f"regime: {asset_class} probe failed ({e})")
    _cache[key] = (now, regime)
    return regime


def regime_matches(filter_regime: str | None, regime_key: str | None) -> bool:
    """Lesson filter match. A filter may pin all three axes ("high/down/pos")
    or use '*' wildcards per axis ("high/*/*")."""
    if not filter_regime or not regime_key:
        return False
    want = filter_regime.split("/")
    have = regime_key.split("/")
    if len(want) != 3 or len(have) != 3:
        return filter_regime == regime_key
    return all(w in ("*", h) for w, h in zip(want, have))

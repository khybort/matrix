"""Similar-setup memory (docs/AUTONOMY_PLAN.md P2.5).

Lessons generalise by (symbol, side, regime) bucket; this module answers the
finer question the agent could never ask before: *"the last k times this
symbol looked like THIS — flow, book, funding, OI, regime — and we took this
side, what happened?"*

No embedding provider is needed: every prediction already stores its feature
snapshot in `predictions.context.features`, so `setup_vector()` projects that
snapshot into a fixed 14-dim, roughly unit-scaled vector and the nearest
neighbours are found by cosine similarity over the recent history of the same
symbol/strategy/side (rows cached per process for ROW_CACHE_TTL_S). When the
migration chain opens this becomes a pgvector column; the vector definition
below is the contract either way.
"""

from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from loguru import logger
from sqlalchemy import text

from matrix_shared.db import shared_session_scope
from matrix_shared.edge_study import episode_groups
from matrix_shared.stats import wilson_bounds

ENABLED = os.environ.get("MATRIX_SETUP_MEMORY", "1") == "1"
MIN_N = int(os.environ.get("MATRIX_SETUP_MEMORY_MIN_N", "10"))
MIN_SIM = float(os.environ.get("MATRIX_SETUP_MEMORY_MIN_SIM", "0.85"))
K = int(os.environ.get("MATRIX_SETUP_MEMORY_K", "20"))
LOOKBACK_DAYS = int(os.environ.get("MATRIX_SETUP_MEMORY_DAYS", "60"))
MAX_ROWS = int(os.environ.get("MATRIX_SETUP_MEMORY_MAX_ROWS", "1500"))
ROW_CACHE_TTL_S = float(os.environ.get("MATRIX_SETUP_MEMORY_TTL_S", "300"))
# Decision thresholds on the Wilson interval of the neighbours' win rate.
GOOD_WR_LOWER = float(os.environ.get("MATRIX_SETUP_MEMORY_GOOD_WR", "0.55"))
BAD_WR_UPPER = float(os.environ.get("MATRIX_SETUP_MEMORY_BAD_WR", "0.45"))
# Fallback tier: when the symbol's own history is too thin, borrow neighbours
# from every symbol of the asset class at a stricter similarity (features are
# normalised, so "this kind of setup" transfers) — evidence arrives weeks sooner.
CROSS_SYMBOL_MIN_SIM = float(os.environ.get("MATRIX_SETUP_MEMORY_CROSS_MIN_SIM", "0.95"))

DIM = 14
_REGIME_AXIS = {"low": -1.0, "mid": 0.0, "high": 1.0,
                "down": -1.0, "flat": 0.0, "up": 1.0,
                "neg": -1.0, "neutral": 0.0, "pos": 1.0}


def _f(v: Any, default: float = 0.0) -> float:
    if v is None or v == "":
        return default
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default


def _clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def setup_vector(features: dict[str, Any]) -> list[float]:
    """Project a `predictions.context.features` dict (or the agent's live
    feature dump) into the 14-dim setup vector. Pure and total: missing keys
    contribute 0, so old rows and new rows remain comparable."""
    regime = str(features.get("regime") or "unknown").split("/")
    regime += ["unknown"] * (3 - len(regime))
    graph = features.get("graph") or {}
    return [
        _clamp((_f(features.get("buy_share_60s"), 0.5) - 0.5) * 2),
        _clamp(math.log10(_f(features.get("n_trades_60s")) + 1) / 3),
        _clamp(math.log10(max(_f(features.get("notional_60s_usd")), 0.0) + 1) / 6),
        _clamp(_f(features.get("spread_bps")) / 20),
        _clamp((_f(features.get("ob_imbalance_top5"), 0.5) - 0.5) * 2),
        _clamp(_f(features.get("funding_rate")) / 0.001),
        _clamp(_f(features.get("oi_delta_pct_5m")) / 0.02),
        _clamp(_f(features.get("price_change_pct_5m")) / 0.01),
        _clamp(_f(features.get("n_news_1h")) / 5),
        _REGIME_AXIS.get(regime[0], 0.0),
        _REGIME_AXIS.get(regime[1], 0.0),
        _REGIME_AXIS.get(regime[2], 0.0),
        _clamp(_f(graph.get("direct_polarity"))),
        _clamp(_f(graph.get("contextual_polarity"))),
    ]


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


@dataclass(frozen=True, slots=True)
class SetupStats:
    n: int
    wins: int
    win_rate: float
    wr_lower: float
    wr_upper: float
    avg_pnl_pct: float
    mean_sim: float
    candidates: int
    scope: str = "symbol"

    @property
    def verdict(self) -> str:
        """good | bad | neutral — only ever non-neutral with n >= MIN_N."""
        if self.n < MIN_N:
            return "neutral"
        if self.wr_lower >= GOOD_WR_LOWER and self.avg_pnl_pct > 0:
            return "good"
        if self.wr_upper <= BAD_WR_UPPER and self.avg_pnl_pct < 0:
            return "bad"
        return "neutral"

    def as_dict(self) -> dict[str, Any]:
        return {"n": self.n, "wins": self.wins, "win_rate": round(self.win_rate, 3),
                "wr_lower": round(self.wr_lower, 3), "wr_upper": round(self.wr_upper, 3),
                "avg_pnl_pct": round(self.avg_pnl_pct, 5), "mean_sim": round(self.mean_sim, 3),
                "candidates": self.candidates, "scope": self.scope, "verdict": self.verdict}


EMPTY = SetupStats(0, 0, 0.0, 0.0, 1.0, 0.0, 0.0, 0)


def summarize(rows: list[tuple[list[float], float]], vec: list[float], *, k: int = K,
              min_sim: float = MIN_SIM, scope: str = "symbol") -> SetupStats:
    """Nearest-k neighbours (cosine >= min_sim) of `vec` among (vector, pnl_pct) rows."""
    if not rows:
        return EMPTY
    scored = sorted(((cosine(v, vec), pnl) for v, pnl in rows), key=lambda t: t[0], reverse=True)
    near = [(s, p) for s, p in scored[:k] if s >= min_sim]
    if not near:
        return SetupStats(0, 0, 0.0, 0.0, 1.0, 0.0, 0.0, len(rows), scope)
    wins = sum(1 for _, p in near if p > 0)
    n = len(near)
    lo, hi = wilson_bounds(wins, n)
    return SetupStats(n, wins, wins / n, lo, hi, sum(p for _, p in near) / n,
                      sum(s for s, _ in near) / n, len(rows), scope)


# ------------------------------------------------------------------ storage

_row_cache: dict[tuple[str, str, str, str], tuple[float, list[tuple[list[float], float]]]] = {}


def clear_cache() -> None:
    _row_cache.clear()


def episode_rows(raw: list[dict]) -> list[tuple[list[float], float]]:
    """One (setup vector, pnl_pct) per episode: the bet's features and the
    mean return of its fills.

    A strategy that re-emits the same bet every tick leaves a row per re-fill
    with near-identical features, so before 2026-10-09 one call re-filled ten
    times filled ten of the K neighbour slots and its single outcome read as a
    Wilson-tight win rate (`edge_study.episode_groups`).
    """
    out: list[tuple[list[float], float]] = []
    for g in episode_groups(sorted(raw, key=lambda r: r["generated_at"])):
        feats = g[0]["features"]
        if isinstance(feats, dict):
            out.append((setup_vector(feats), sum(float(r["pnl_pct"] or 0) for r in g) / len(g)))
    return out


async def _history(symbol: str, asset_class: str, strategy_id: str, side: str) -> list[tuple[list[float], float]]:
    key = (symbol, asset_class, strategy_id, side)
    hit = _row_cache.get(key)
    now = time.monotonic()
    if hit and now - hit[0] < ROW_CACHE_TTL_S:
        return hit[1]
    since = datetime.now(UTC) - timedelta(days=LOOKBACK_DAYS)
    sym_clause = "" if symbol == "*" else "AND p.symbol = :sym "
    async with shared_session_scope() as session:
        res = await session.execute(text(
            "SELECT p.context->'features' AS features, o.pnl_pct, p.strategy_id, p.asset_class, "
            "       p.symbol, p.side, p.generated_at, p.horizon_seconds "
            "FROM predictions p JOIN outcomes o ON o.prediction_id = p.id "
            f"WHERE p.asset_class = :ac AND p.strategy_id = :sid {sym_clause}"
            "AND p.side = :side AND p.generated_at >= :since "
            "AND o.reason <> 'orphan_flat_close' "
            "AND p.context->'features' IS NOT NULL "
            "ORDER BY p.generated_at DESC LIMIT :lim"
        ), {"sym": symbol, "ac": asset_class, "sid": strategy_id, "side": side,
            "since": since, "lim": MAX_ROWS})
        rows = episode_rows([dict(r) for r in res.mappings().all()])
    _row_cache[key] = (now, rows)
    return rows


async def similar_setups(*, symbol: str, asset_class: str, strategy_id: str, side: str,
                         features: dict[str, Any]) -> SetupStats:
    """What happened the last K times this symbol/strategy took `side` in a
    setup resembling `features`. Advisory: any failure returns EMPTY."""
    if not ENABLED or side not in ("long", "short"):
        return EMPTY
    vec = setup_vector(features)
    try:
        own = summarize(await _history(symbol, asset_class, strategy_id, side), vec)
        if own.n >= MIN_N:
            return own
        pooled = summarize(await _history("*", asset_class, strategy_id, side), vec,
                           min_sim=CROSS_SYMBOL_MIN_SIM, scope="asset_class")
    except Exception as e:  # noqa: BLE001 — memory is advisory, never blocks a tick
        logger.debug(f"setup_memory: history lookup failed for {symbol}/{side} ({e})")
        return EMPTY
    return pooled if pooled.n > own.n else own


def confidence_adjustment(stats: SetupStats, confidence: Decimal) -> Decimal:
    """`good` → +0.10 (capped at 1); `bad` → halve; neutral → unchanged."""
    if stats.verdict == "good":
        return min(Decimal("1"), confidence + Decimal("0.10"))
    if stats.verdict == "bad":
        return (confidence / 2).quantize(Decimal("0.00001"))
    return confidence

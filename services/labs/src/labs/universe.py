"""Dynamic universe manager — score a candidate pool, keep the high-potential
top-N active, churn out faded symbols.

Layer 1 of the asset-selection improvement. Lives in labs (same autonomous
scan + flip + log home as promote.py). Entirely PAPER-layer: it only ever writes
`tradable_symbols` (and, for BIST, `bist_symbols.active`) — never risk caps, never
the live-execution gate. Autonomy is gated by env flags and bounded by guards
(hard min-liquidity floor, enter/exit hysteresis band, min-hold, per-scan churn cap).

Modes:
  - shadow (default): score every pool symbol, write scores into `tradable_symbols`,
    and LOG the intended activate/deactivate diff — but do NOT flip `active`.
  - enforce (UNIVERSE_MANAGER_ENFORCE=true): additionally apply the diff through
    the guards.

The candidate pool + its liquidity/volatility/momentum/microstructure inputs come
from `screener_universe_snapshot` (LOCAL), populated exchange-wide by the screener
every poll — so a never-traded symbol can be scored without ingesting its bars.
Realized per-symbol edge is layered in by `_edge_map` (PR4); until then it is
neutral (0.5) for every symbol.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from loguru import logger
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from matrix_shared import session_scope, shared_session_scope
from matrix_shared.models import TradableSymbol

# ── Config (env-overridable; tune without a migration) ───────────────────────


@dataclass(slots=True)
class UniverseConfig:
    target_active_n: int
    enter_floor: float
    exit_floor: float           # < enter_floor; the gap IS the anti-thrash band
    min_hold_seconds: float
    max_churn_per_scan: int     # cap on activations and on deactivations, each
    min_liquidity_usd: float    # hard floor — overrides score/hold
    edge_lookback_days: float = 14.0  # realized-edge window
    weights: dict[str, float] = field(default_factory=dict)


# Empirical-Bayes pseudo-count: a symbol needs ~this many closed trades before
# its realized edge meaningfully moves off neutral (same scale as the lessons
# synthesizer's MIN_N_PER_BUCKET).
EDGE_SHRINK_K = float(os.environ.get("UNIVERSE_EDGE_SHRINK_K", "20"))


def _envf(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


def _envi(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


def _config(asset_class: str) -> UniverseConfig:
    if asset_class == "crypto":
        return UniverseConfig(
            target_active_n=_envi("UNIVERSE_CRYPTO_TARGET_N", 25),
            enter_floor=_envf("UNIVERSE_CRYPTO_ENTER_FLOOR", 0.70),
            exit_floor=_envf("UNIVERSE_CRYPTO_EXIT_FLOOR", 0.40),
            min_hold_seconds=_envf("UNIVERSE_CRYPTO_MIN_HOLD_S", 21600),  # 6h
            max_churn_per_scan=_envi("UNIVERSE_CRYPTO_MAX_CHURN", 6),
            min_liquidity_usd=_envf("UNIVERSE_CRYPTO_MIN_LIQUIDITY_USD", 5_000_000),
            edge_lookback_days=_envf("UNIVERSE_CRYPTO_EDGE_LOOKBACK_D", 14),
            weights={"L": 0.35, "V": 0.15, "M": 0.10, "E": 0.30, "U": 0.10},
        )
    # BIST (scored in PR9; config present so the shape is uniform)
    return UniverseConfig(
        target_active_n=_envi("UNIVERSE_BIST_TARGET_N", 60),
        enter_floor=_envf("UNIVERSE_BIST_ENTER_FLOOR", 0.65),
        exit_floor=_envf("UNIVERSE_BIST_EXIT_FLOOR", 0.40),
        min_hold_seconds=_envf("UNIVERSE_BIST_MIN_HOLD_S", 172800),  # ~2 sessions
        max_churn_per_scan=_envi("UNIVERSE_BIST_MAX_CHURN", 10),
        min_liquidity_usd=_envf("UNIVERSE_BIST_MIN_LIQUIDITY_USD", 2_000_000),
        edge_lookback_days=_envf("UNIVERSE_BIST_EDGE_LOOKBACK_D", 30),
        weights={"L": 0.35, "V": 0.20, "M": 0.15, "E": 0.30},
    )


def enabled() -> bool:
    """Whether the labs loop should run the universe rhythm at all (default off)."""
    return os.environ.get("UNIVERSE_MANAGER_ENABLED", "").strip().lower() == "true"


def enforce_enabled() -> bool:
    """Whether to actually flip `active` (default off → shadow)."""
    return os.environ.get("UNIVERSE_MANAGER_ENFORCE", "").strip().lower() == "true"


# ── Scoring ──────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class ScoredSymbol:
    symbol: str
    score: float
    liquidity: float
    components: dict
    rank: int = 0


@dataclass(slots=True)
class ReconcileReport:
    asset_class: str
    scored: int
    would_activate: list[str]
    would_deactivate: list[str]
    activated: int
    deactivated: int
    n_active_after: int
    enforced: bool


def _pct_rank(values: list[float]) -> list[float]:
    """Cross-sectional percentile rank in [0,1]; tied values share the mean rank."""
    n = len(values)
    if n <= 1:
        return [0.5] * n
    order = sorted(range(n), key=lambda i: values[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg_pos = sum(range(i, j + 1)) / (j - i + 1)
        for k in range(i, j + 1):
            ranks[order[k]] = avg_pos / (n - 1)
        i = j + 1
    return ranks


def _score_pool(
    pool: list[dict], edge_map: dict[str, float], cfg: UniverseConfig
) -> list[ScoredSymbol]:
    """Percentile-rank each component over the pool, blend into a composite [0,1]."""
    rows: list[dict] = []
    for r in pool:
        last = r.get("last_price")
        turnover = r.get("turnover24h")
        if last is None or last <= 0 or turnover is None:
            continue  # can't score without price + liquidity
        if float(turnover) < cfg.min_liquidity_usd:
            continue  # below the hard floor — never tradable, don't pollute ranks
        high = r.get("high24h") or last
        low = r.get("low24h") or last
        rows.append({
            "symbol": r["symbol"],
            "liq": float(turnover),
            "vol": float((high - low) / last) if last else 0.0,
            "mom": abs(float(r.get("price24h_pct") or 0.0)),
            "micro": abs(float(r.get("funding_rate") or 0.0)),
        })
    if not rows:
        return []

    L = _pct_rank([x["liq"] for x in rows])
    V = _pct_rank([x["vol"] for x in rows])
    M = _pct_rank([x["mom"] for x in rows])
    U = _pct_rank([x["micro"] for x in rows])
    w = cfg.weights

    scored: list[ScoredSymbol] = []
    for i, x in enumerate(rows):
        e = edge_map.get(x["symbol"], 0.5)  # neutral until PR4
        comp = {
            "L": round(L[i], 4), "V": round(V[i], 4), "M": round(M[i], 4),
            "E": round(e, 4), "U": round(U[i], 4),
            "raw": {"liquidity_usd": x["liq"], "range": round(x["vol"], 6),
                    "abs_mom": round(x["mom"], 6), "abs_funding": round(x["micro"], 8)},
        }
        score = (
            w.get("L", 0) * L[i] + w.get("V", 0) * V[i] + w.get("M", 0) * M[i]
            + w.get("E", 0) * e + w.get("U", 0) * U[i]
        )
        scored.append(ScoredSymbol(
            symbol=x["symbol"], score=round(score, 6), liquidity=x["liq"], components=comp,
        ))

    scored.sort(key=lambda s: s.score, reverse=True)
    for rank, s in enumerate(scored):
        s.rank = rank
    return scored


# ── Data access ──────────────────────────────────────────────────────────────


async def _fetch_crypto_pool() -> list[dict]:
    """All USDT-perp symbols the screener saw last poll (LOCAL tier)."""
    async with session_scope() as db:
        res = await db.execute(text("""
            SELECT symbol, turnover24h, price24h_pct, high24h, low24h,
                   last_price, oi_value, funding_rate
            FROM screener_universe_snapshot
        """))
        return [dict(r._mapping) for r in res]


def _shrink_edge(n: int, raw_avg_score: float) -> float:
    """Empirical-Bayes per-symbol edge → [0,1], shrunk toward neutral (0.5).

    raw_avg_score is the mean outcome score in [-1,1] (already cap-normalised by
    the paper engine). With n=0 the symbol returns exactly 0.5 (neutral, not
    penalised) so a never-traded symbol can still be activated on liquidity +
    momentum without being favoured on edge it hasn't earned.
    """
    if n <= 0:
        return 0.5
    shrunk = (n * raw_avg_score) / (n + EDGE_SHRINK_K)  # toward 0
    return max(0.0, min(1.0, (shrunk + 1.0) / 2.0))


async def _edge_map(asset_class: str, lookback_days: float) -> dict[str, float]:
    """Per-symbol realized edge in [0,1] from closed outcomes (SHARED tier).

    Symbols with no outcomes are simply absent → the scorer treats them as
    neutral (0.5). All strategies count (a symbol's edge is cross-strategy).
    """
    since = datetime.now(UTC) - timedelta(days=lookback_days)
    async with shared_session_scope() as db:
        res = await db.execute(text("""
            SELECT p.symbol AS symbol,
                   COUNT(o.id) AS n,
                   AVG(o.score) AS avg_score
            FROM outcomes o
            JOIN predictions p ON p.id = o.prediction_id
            WHERE p.asset_class = :ac AND o.observed_at >= :since
            GROUP BY p.symbol
        """), {"ac": asset_class, "since": since})
        out: dict[str, float] = {}
        for row in res:
            n = int(row.n or 0)
            if n <= 0:
                continue
            out[row.symbol] = _shrink_edge(n, float(row.avg_score or 0.0))
        return out


async def _write_scores(asset_class: str, scored: list[ScoredSymbol]) -> None:
    """Upsert score/components/rank for every pool symbol (does NOT touch active)."""
    now = datetime.now(UTC)
    async with shared_session_scope() as db:
        for s in scored:
            stmt = pg_insert(TradableSymbol).values(
                asset_class=asset_class,
                symbol=s.symbol,
                active=False,
                score=s.score,
                components_json=s.components,
                liquidity_usd=Decimal(str(round(s.liquidity, 2))),
                rank=s.rank,
                last_scored_at=now,
            ).on_conflict_do_update(
                index_elements=["asset_class", "symbol"],
                set_={
                    "score": s.score,
                    "components_json": s.components,
                    "liquidity_usd": Decimal(str(round(s.liquidity, 2))),
                    "rank": s.rank,
                    "last_scored_at": now,
                },
            )
            await db.execute(stmt)


async def _load_active(asset_class: str) -> dict[str, datetime | None]:
    """Currently-active symbols → became_active_at (for min-hold checks)."""
    async with shared_session_scope() as db:
        res = await db.execute(
            select(TradableSymbol.symbol, TradableSymbol.became_active_at).where(
                TradableSymbol.asset_class == asset_class,
                TradableSymbol.active.is_(True),
            )
        )
        return {row[0]: row[1] for row in res}


async def _apply_flips(
    asset_class: str, activate: list[str], deactivate: list[str]
) -> None:
    now = datetime.now(UTC)
    async with shared_session_scope() as db:
        for sym in activate:
            await db.execute(
                pg_insert(TradableSymbol).values(
                    asset_class=asset_class, symbol=sym, active=True, became_active_at=now,
                ).on_conflict_do_update(
                    index_elements=["asset_class", "symbol"],
                    set_={"active": True, "became_active_at": now},
                )
            )
        for sym in deactivate:
            await db.execute(
                pg_insert(TradableSymbol).values(
                    asset_class=asset_class, symbol=sym, active=False, became_active_at=None,
                ).on_conflict_do_update(
                    index_elements=["asset_class", "symbol"],
                    set_={"active": False, "became_active_at": None},
                )
            )


# ── Reconcile ────────────────────────────────────────────────────────────────


def _plan_flips(
    scored: list[ScoredSymbol],
    current: dict[str, datetime | None],
    cfg: UniverseConfig,
    now: datetime,
) -> tuple[list[str], list[str]]:
    """Pure selection: given scores + the current active set, return the
    (to_activate, to_deactivate) symbol lists after enter/exit hysteresis,
    the hard liquidity floor, min-hold, target-N and per-scan churn caps.
    """
    by_symbol = {s.symbol: s for s in scored}

    # Enter: inactive, score >= enter_floor, above hard liquidity floor.
    slots_free = max(0, cfg.target_active_n - len(current))
    enter = [
        s.symbol for s in scored
        if s.symbol not in current
        and s.score >= cfg.enter_floor
        and s.liquidity >= cfg.min_liquidity_usd
    ][:slots_free]

    # Exit: active & (gone from pool OR below liquidity floor OR below exit_floor),
    # respecting min-hold (a liquidity-floor breach overrides the hold).
    exit_: list[str] = []
    for sym, became in current.items():
        s = by_symbol.get(sym)
        below_floor = s is None or s.liquidity < cfg.min_liquidity_usd
        faded = s is not None and s.score < cfg.exit_floor
        if not (below_floor or faded):
            continue
        if not below_floor and became is not None:
            if (now - became).total_seconds() < cfg.min_hold_seconds:
                continue  # too young to drop on score alone
        exit_.append(sym)

    return enter[: cfg.max_churn_per_scan], exit_[: cfg.max_churn_per_scan]


async def score_and_reconcile(
    asset_class: str = "crypto", *, enforce: bool | None = None
) -> ReconcileReport:
    """Score the candidate pool, write scores, and (optionally) churn the active set."""
    if enforce is None:
        enforce = enforce_enabled()
    cfg = _config(asset_class)

    if asset_class == "crypto":
        pool = await _fetch_crypto_pool()
    else:
        pool = []  # BIST lands in PR9

    if not pool:
        logger.info(f"universe[{asset_class}]: empty candidate pool, skipping")
        return ReconcileReport(asset_class, 0, [], [], 0, 0, 0, enforce)

    edge_map = await _edge_map(asset_class, cfg.edge_lookback_days)
    scored = _score_pool(pool, edge_map, cfg)
    await _write_scores(asset_class, scored)

    current = await _load_active(asset_class)
    now = datetime.now(UTC)
    capped_enter, capped_exit = _plan_flips(scored, current, cfg, now)

    activated = deactivated = 0
    if enforce and (capped_enter or capped_exit):
        await _apply_flips(asset_class, capped_enter, capped_exit)
        activated, deactivated = len(capped_enter), len(capped_exit)

    n_active_after = len(current) + activated - deactivated
    verb = "applied" if enforce else "shadow"
    logger.info(
        f"universe[{asset_class}] {verb}: scored={len(scored)} "
        f"+{len(capped_enter)} -{len(capped_exit)} "
        f"(active={n_active_after}/{cfg.target_active_n}); "
        f"enter={capped_enter[:8]} exit={capped_exit[:8]}"
    )

    return ReconcileReport(
        asset_class=asset_class,
        scored=len(scored),
        would_activate=capped_enter,
        would_deactivate=capped_exit,
        activated=activated,
        deactivated=deactivated,
        n_active_after=n_active_after,
        enforced=enforce,
    )

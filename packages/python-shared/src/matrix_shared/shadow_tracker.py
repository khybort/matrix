"""Is a shadow bet earning what its research said it would?

Every strategy but one was measured without edge after costs (2026-10). The
one left, `neg_funding_carry`, was registered with a band before it traded:
replay of the entry filter gave +176 / +293 bps per kept episode (train /
holdout, median far below the mean), and a mean of +30 bps or less would mean
borrow and book costs ate the edge (docs/wiki/signal-research-2026-10.md,
"Shadow-book hardening"). Twice before, nobody compared live results with
what the research predicted: inflated metrics stood for weeks, and the system
went ten days without a trade while every freshness signal stayed green
(docs/wiki/incidents.md). This module is that comparison, run for any shadow
strategy that carries a band.

The band lives next to the strategy, in `strategy_configs.params.shadow_band`
(DB, authoritative). `DEFAULT_BANDS` holds the pre-registered values and is
used only when the row has no band, so a params rewrite cannot silence the
tracker.

Units are episodes (`edge_study.episode_groups`), never rows. For a closed
carry the paper engine stores pnl_usd net of the book cost
(`context.book_close.total_bps`) and borrow (`context.borrow_charged_usd`), so
funding = pnl + book + borrow. Verdicts, most severe first:

  broken      no episode opened for `stale_hours` while the watchlist had at
              least `min_qualifying` qualifying settlements, or a closed
              episode with a decomposition anomaly (zero funding over a hold
              that crossed a settlement, borrow not charged — a missing quote or
              charge, or nothing charged on a non-zero quote; a zero quote or an
              all-zero recorded series is genuine — book not charged)
  collecting  fewer than `min_episodes` closed episodes
  below_band  mean net ≤ `floor_bps`
  on_track    otherwise

Two pre-registered A/B comparisons ride on the same episodes (signal-research-2026-10.md,
"Borrow measurement" and "Live path and funding decay"): the borrow source of
each close (`series` measures quoted borrow; `stressed_entry` and `mixed` do
not, or only partly) and the entry-rule arms recorded on every signal
(`flat_keep`: the old quote x 3 rule; `decay_keep`: the funding-decay gate).
Once `revisit_series_episodes` closed episodes are `series`, `revisit` states
which of the three revisit rules holds and the env change it implies; notify
sends that once as `review_due`. The tracker never changes the env itself.

`evaluate` is pure; `collect` does the I/O.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger
from sqlalchemy import text

from matrix_shared.edge_study import episode_groups, one_per_episode

COLLECTING = "collecting"
ON_TRACK = "on_track"
BELOW_BAND = "below_band"
BROKEN = "broken"

# Pre-registered 2026-10-09 (signal-research-2026-10.md, "Shadow-book
# hardening"): filter kept, borrow x3 — train +175.6 bps (median +12.7),
# holdout +292.5 (median +96.8); +30.9 at borrow x10 is the "costs ate it"
# line. ~50 episodes a week expected once ingestion covers the watchlist.
DEFAULT_BANDS: dict[tuple[str, str], dict[str, Any]] = {
    ("neg_funding_carry", "crypto"): {
        "since": "2026-10-09T13:30:00+00:00",  # entry filter + book-priced paper accounting
        "expected_bps_low": 100.0,
        "expected_bps_high": 300.0,
        "floor_bps": 30.0,
        "min_episodes": 20,
        "stale_hours": 72,
        "min_qualifying": 10,
        "qualify_universe": "crypto_carry",  # tradable_symbols asset_class
        "qualify_funding_max": -0.0008,  # settled rate at or below this qualifies
        "components": ["funding", "borrow", "book"],
        "expected_per_week": 50,
        "source": "docs/wiki/signal-research-2026-10.md#shadow-book-hardening",
        # Borrow measurement / funding decay revisit rules (2026-10-09).
        "revisit_series_episodes": 30,
        "revisit_hold_stress_env": "MATRIX_NFC_BORROW_HOLD_STRESS",
        "revisit_borrow_model_env": "MATRIX_NFC_BORROW_MODEL",
        "revisit_expected_model_env": "MATRIX_NFC_EXPECTED_MODEL",
    },
}

# A carry that crossed no settlement books no funding legitimately; 9 h covers
# the longest (8 h) interval plus the 1 h entry lag.
_MIN_HOLD_FOR_FUNDING_H = 9.0
_ZERO_FUNDING_BPS = 0.01


@dataclass(slots=True)
class Episode:
    symbol: str
    opened_at: datetime
    closed: bool
    notional_usd: float = 0.0
    pnl_usd: float = 0.0
    held_h: float = 0.0
    funding_usd: float | None = None
    borrow_usd: float | None = None
    book_usd: float | None = None
    anomalies: list[str] = field(default_factory=list)
    # series | stressed_entry | mixed (re-emissions disagreeing also = mixed) | unknown
    borrow_source: str = "unknown"
    # notional-weighted hold-mean series borrow / entry quote, over rows with a series mean
    borrow_ratio: float | None = None
    # entry-rule verdicts recorded on the bet (first signal); None = not recorded
    flat_keep: bool | None = None
    naive_keep: bool | None = None
    decay_keep: bool | None = None

    def bps(self, usd: float | None) -> float | None:
        if usd is None or not self.notional_usd:
            return None
        return usd / self.notional_usd * 10_000

    @property
    def net_bps(self) -> float | None:
        return self.bps(self.pnl_usd) if self.closed else None


def _f(v: Any) -> float | None:
    if v is None or v == "":
        return None
    return float(v)


def _b(v: Any) -> bool | None:
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return v
    return str(v).lower() == "true"


def _source(g: list[dict]) -> str:
    srcs = {r.get("borrow_source") or "unknown" for r in g}
    return srcs.pop() if len(srcs) == 1 else "mixed"


def _ratio(g: list[dict]) -> float | None:
    num = den = 0.0
    for r in g:
        mean, quote = _f(r.get("borrow_series_mean_hourly")), _f(r.get("borrow_rate_hourly"))
        if mean is None or not quote:
            continue
        w = float(r["notional_usd"])
        num += w * mean / quote
        den += w
    return num / den if den else None


def _utc(ts: datetime) -> datetime:
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def _borrow_charged(r: dict, charged: float | None) -> bool:
    """Was borrow charged as the recorded quotes say it should be? A missing
    quote or charge field means the close never ran the borrow path. A zero
    charge is genuine only when the quotes were zero: the entry quote itself
    (every fallback hour is quote x stress = 0), or every hour priced from the
    recorded series at a mean of zero. A positive quote that charged nothing
    means the fallback was not applied."""
    quote = _f(r.get("borrow_rate_hourly"))
    if charged is None or quote is None or charged < 0:
        return False
    if charged > 0:
        return True
    if quote == 0:
        return True
    return r.get("borrow_source") == "series" and _f(r.get("borrow_series_mean_hourly")) == 0


def decompose(rows: list[dict], components: Iterable[str] = ("funding", "borrow", "book")) -> list[Episode]:
    """Group filled rows into episodes and split each closed one into funding,
    borrow and book cost (USD; costs positive). Rows carry the episode fields
    plus notional_usd, opened_at, closed_at, pnl_usd, borrow_rate_hourly,
    borrow_charged_usd, book_close_bps."""
    comps = set(components)
    out: list[Episode] = []
    for g in episode_groups(sorted(rows, key=lambda r: r["generated_at"])):
        closed = all(r.get("closed_at") is not None for r in g)
        ep = Episode(symbol=g[0]["symbol"], opened_at=_utc(min(r["opened_at"] for r in g)), closed=closed)
        ep.flat_keep, ep.naive_keep, ep.decay_keep = (_b(g[0].get(k)) for k in ("flat_keep", "naive_keep", "decay_keep"))
        ep.notional_usd = sum(float(r["notional_usd"]) for r in g)
        if not closed:
            out.append(ep)
            continue
        ep.pnl_usd = sum(float(r["pnl_usd"] or 0) for r in g)
        ep.borrow_source = _source(g)
        ep.borrow_ratio = _ratio(g)
        ep.held_h = max((_utc(r["closed_at"]) - _utc(r["opened_at"])).total_seconds() for r in g) / 3600
        borrow = book = 0.0
        borrow_ok = book_ok = True
        for r in g:
            charged = _f(r.get("borrow_charged_usd"))
            if not _borrow_charged(r, charged):
                borrow_ok = False
            borrow += charged or 0.0
            bk = _f(r.get("book_close_bps"))
            if bk is None:
                book_ok = False
            else:
                book += float(r["notional_usd"]) * bk / 10_000
        ep.borrow_usd = borrow if borrow_ok else (borrow or None)
        ep.book_usd = book if book_ok else None
        if book_ok:
            ep.funding_usd = ep.pnl_usd + book + borrow
        if "borrow" in comps and not borrow_ok:
            ep.anomalies.append("borrow_not_charged")
        if "book" in comps and not book_ok:
            ep.anomalies.append("book_not_charged")
        if (
            "funding" in comps
            and ep.funding_usd is not None
            and ep.held_h >= _MIN_HOLD_FOR_FUNDING_H
            and abs(ep.bps(ep.funding_usd) or 0.0) < _ZERO_FUNDING_BPS
        ):
            ep.anomalies.append("zero_funding")
        out.append(ep)
    return out


def clustered_t(values: list[float], clusters: list[Any]) -> float | None:
    """t of the mean with cluster-robust (CR1) standard error."""
    n = len(values)
    groups: dict[Any, float] = {}
    if n < 2:
        return None
    m = sum(values) / n
    for v, c in zip(values, clusters, strict=True):
        groups[c] = groups.get(c, 0.0) + (v - m)
    g = len(groups)
    if g < 2:
        return None
    var = g / (g - 1) * sum(s * s for s in groups.values()) / (n * n)
    if var <= 0:
        return None
    return m / math.sqrt(var)


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


BORROW_SOURCES = ("series", "mixed", "stressed_entry", "unknown")


def arm_stats(eps: list[Episode]) -> dict[str, Any]:
    """n, mean / median net bps and day-clustered t of closed episodes."""
    net = [e.net_bps for e in eps]
    return {
        "n": len(net), "mean_bps": _mean(net),
        "median_bps": statistics.median(net) if net else None,
        "t_day": clustered_t(net, [e.opened_at.date().isoformat() for e in eps]),
    }


def _p90(xs: list[float]) -> float | None:
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    return statistics.quantiles(xs, n=10, method="inclusive")[8]


def revisit(closed: list[Episode], band: dict[str, Any]) -> dict[str, Any] | None:
    """The pre-registered revisit rules over closed `series` episodes only
    (stressed_entry / mixed closes charged an assumed borrow and cannot judge
    it). None when the band registers no revisit. Each rule:
    {rule, holds, env, value, text}; `due` once the series count reaches the
    threshold."""
    need = band.get("revisit_series_episodes")
    if not need:
        return None
    series = [e for e in closed if e.borrow_source == "series"]
    out: dict[str, Any] = {"series_n": len(series), "need": int(need), "due": len(series) >= int(need), "rules": []}
    ratios = [e.borrow_ratio for e in series if e.borrow_ratio is not None]
    p90 = _p90(ratios)
    env = band.get("revisit_hold_stress_env", "MATRIX_NFC_BORROW_HOLD_STRESS")
    value = f"{math.ceil(round(p90 * 100, 6)) / 100:.2f}" if p90 is not None and p90 > 1.0 else None
    out["rules"].append({
        "rule": "hold_stress", "holds": value is not None, "env": env, "value": value,
        "p90": p90, "n": len(ratios), "median": statistics.median(ratios) if ratios else None,
    })
    for rule, key, env_key, default_env, target in (
        ("borrow_model", "flat_keep", "revisit_borrow_model_env", "MATRIX_NFC_BORROW_MODEL", "flat"),
        ("expected_model", "decay_keep", "revisit_expected_model_env", "MATRIX_NFC_EXPECTED_MODEL", "decay"),
    ):
        # flat_keep=false: kept only by the depth borrow model.
        # decay_keep=false (naive kept): kept only by the naive expectation.
        arm = [e for e in series if getattr(e, key) is False and e.naive_keep is not False]
        st = arm_stats(arm)
        out["rules"].append({
            "rule": rule, "holds": st["n"] > 0 and st["mean_bps"] <= 0.0,
            "env": band.get(env_key, default_env), "value": target, "arm": f"{key}=false", **st,
        })
    return out


def evaluate(
    rows: list[dict],
    band: dict[str, Any],
    *,
    now: datetime,
    qualifying: int | None = None,
    strategy_id: str = "",
    asset_class: str = "",
) -> dict[str, Any]:
    """Pure: the report and verdict for one strategy's filled rows."""
    eps = decompose(rows, band.get("components") or ())
    closed = [e for e in eps if e.closed]
    net = [e.net_bps for e in closed]
    days = [e.opened_at.date().isoformat() for e in closed]
    stale_h = float(band.get("stale_hours", 72))
    since = _utc(datetime.fromisoformat(band["since"])) if band.get("since") else None
    last_open = max((e.opened_at for e in eps), default=None)
    ref = last_open or since
    gap_h = (now - ref).total_seconds() / 3600 if ref else None

    rep: dict[str, Any] = {
        "strategy_id": strategy_id, "asset_class": asset_class,
        "opened": len(one_per_episode(sorted(rows, key=lambda r: r["generated_at"]))),
        "closed": len(closed), "open_now": len(eps) - len(closed),
        "n_raw": len(rows),
        "mean_bps": _mean(net), "median_bps": statistics.median(net) if net else None,
        "t_day": clustered_t(net, days), "n_days": len(set(days)),
        "win_rate": sum(1 for x in net if x > 0) / len(net) if net else None,
        "funding_bps": _mean([e.bps(e.funding_usd) for e in closed if e.funding_usd is not None]),
        "borrow_bps": _mean([e.bps(e.borrow_usd) for e in closed if e.borrow_usd is not None]),
        "book_bps": _mean([e.bps(e.book_usd) for e in closed if e.book_usd is not None]),
        "pnl_usd": sum(e.pnl_usd for e in closed),
        "last_open_age_h": (now - last_open).total_seconds() / 3600 if last_open else None,
        "qualifying": qualifying,
        "band": band,
        "reasons": [],
    }

    reasons: list[str] = []
    min_q = int(band.get("min_qualifying", 0))
    # Unknown opportunity count only flags a band that needs none.
    if gap_h is not None and gap_h > stale_h and (qualifying or 0) >= min_q:
        reasons.append("stale")
    anomalous: dict[str, list[str]] = {}
    for e in closed:
        for a in e.anomalies:
            anomalous.setdefault(a, []).append(e.symbol)
    reasons.extend(sorted(anomalous))
    rep["anomalies"] = anomalous
    rep["gap_h"] = gap_h

    if reasons:
        v = BROKEN
    elif len(closed) < int(band.get("min_episodes", 20)):
        v = COLLECTING
    elif rep["mean_bps"] <= float(band.get("floor_bps", 0.0)):
        v = BELOW_BAND
    else:
        v = ON_TRACK
    rep["verdict"] = v
    rep["reasons"] = reasons
    rep["by_borrow_source"] = {
        src: {"n": len(xs), "mean_bps": _mean([e.net_bps for e in xs])}
        for src in BORROW_SOURCES
        if (xs := [e for e in closed if e.borrow_source == src])
    }
    rep["arms"] = {
        key: {str(val).lower(): arm_stats([e for e in closed if getattr(e, key) is val]) for val in (True, False)}
        for key in ("flat_keep", "decay_keep")
    }
    rep["revisit"] = revisit(closed, band)
    return rep


def _bps(x: float | None, signed: bool = True) -> str:
    if x is None:
        return "n/a"
    return f"{x:+.1f}" if signed else f"{x:.1f}"


def _age(h: float | None) -> str:
    if h is None:
        return "never"
    if h < 48:
        return f"{h:.0f}h ago"
    return f"{h / 24:.1f}d ago"


def _reason_text(rep: dict[str, Any]) -> list[str]:
    out = []
    b = rep["band"]
    for r in rep["reasons"]:
        if r == "stale":
            q = rep.get("qualifying")
            out.append(
                f"no episode opened for {rep['gap_h']:.0f}h (> {b.get('stale_hours', 72)}h)"
                + (f" while the watchlist had {q} qualifying settlements" if q is not None else "")
            )
        else:
            syms = rep["anomalies"].get(r, [])
            label = {
                "zero_funding": "zero funding booked on a closed carry",
                "borrow_not_charged": "borrow not charged",
                "book_not_charged": "book cost not charged (flat cost model)",
            }.get(r, r)
            out.append(f"{label}: {len(syms)} ep ({', '.join(sorted(set(syms))[:4])})")
    return out


def format_line(rep: dict[str, Any]) -> str:
    """One digest line."""
    b = rep["band"]
    head = f"shadow {rep['strategy_id']}/{rep['asset_class']}: {rep['verdict'].upper()}"
    parts = [f"{rep['closed']}/{b.get('min_episodes', 20)} closed ep, {rep['open_now']} open"]
    if rep["closed"]:
        parts.append(
            f"net mean {_bps(rep['mean_bps'])} / median {_bps(rep['median_bps'])} bps "
            f"(t_day {_bps(rep['t_day'], False) if rep['t_day'] is not None else 'n/a'}, {rep['n_days']} d)"
        )
        parts.append(
            f"funding {_bps(rep['funding_bps'])} − borrow {_bps(rep['borrow_bps'], False)} "
            f"− book {_bps(rep['book_bps'], False)} bps"
        )
    parts.append(
        f"band {b.get('expected_bps_low', 0):+.0f}…{b.get('expected_bps_high', 0):+.0f}, "
        f"floor {b.get('floor_bps', 0):+.0f}"
    )
    if rep["closed"]:
        src = rep.get("by_borrow_source") or {}
        parts.append(
            "borrow src " + "/".join(str(src.get(k, {}).get("n", 0)) for k in ("series", "mixed", "stressed_entry"))
            + " series/mixed/stressed" + (f" (+{src['unknown']['n']} unknown)" if "unknown" in src else "")
            + (f", review at {rv['series_n']}/{rv['need']} series" if (rv := rep.get("revisit")) else "")
        )
    parts.append(f"last open {_age(rep['last_open_age_h'])}")
    if rep.get("qualifying") is not None:
        parts.append(f"{rep['qualifying']} qualifying settlements/{b.get('stale_hours', 72)}h")
    line = head + " — " + " · ".join(parts)
    if rep["reasons"]:
        line += " · ⚠️ " + "; ".join(_reason_text(rep))
    return line


def format_alert(rep: dict[str, Any], prev: str | None) -> str:
    """Telegram text for a verdict change (or a persisting `broken`)."""
    b = rep["band"]
    name = f"{rep['strategy_id']}/{rep['asset_class']}"
    v = rep["verdict"]
    move = f"{prev} → {v}" if prev and prev != v else v
    icon = {BROKEN: "⚠️", BELOW_BAND: "📉", ON_TRACK: "📈", COLLECTING: "ℹ️"}[v]
    lines = [f"{icon} Shadow {name}: {move}"]
    if v == BROKEN:
        lines += [f"  • {t}" for t in _reason_text(rep)]
    elif v == BELOW_BAND:
        lines.append(
            f"  {rep['closed']} closed ep, mean {_bps(rep['mean_bps'])} bps ≤ floor {b.get('floor_bps', 0):+.0f} "
            f"(pre-registered {b.get('expected_bps_low', 0):+.0f}…{b.get('expected_bps_high', 0):+.0f}): "
            "costs are eating the edge — do not promote."
        )
    elif v == ON_TRACK:
        lines.append(
            f"  {rep['closed']} closed ep, mean {_bps(rep['mean_bps'])} bps above floor "
            f"{b.get('floor_bps', 0):+.0f} (band {b.get('expected_bps_low', 0):+.0f}…"
            f"{b.get('expected_bps_high', 0):+.0f})."
        )
    lines.append("  " + format_line(rep).split(" — ", 1)[1])
    return "\n".join(lines)


def _arm_text(s: dict[str, Any]) -> str:
    t = f"{s['t_day']:.1f}" if s.get("t_day") is not None else "n/a"
    return f"n {s['n']}, mean {_bps(s['mean_bps'])}, median {_bps(s['median_bps'])} bps, t_day {t}"


def format_review(rep: dict[str, Any]) -> str:
    """Telegram text for the one-time `review_due`: which revisit rule holds,
    its numbers, and the env change it implies (never applied here)."""
    rv = rep["revisit"]
    name = f"{rep['strategy_id']}/{rep['asset_class']}"
    lines = [f"🔁 review_due {name}: {rv['series_n']} closed episodes with borrow_source=series (≥ {rv['need']})"]
    src = rep.get("by_borrow_source") or {}
    lines.append("  borrow source: " + ", ".join(
        f"{k} n {v['n']} mean {_bps(v['mean_bps'])}" for k, v in src.items()))
    holding = []
    for r in rv["rules"]:
        if r["rule"] == "hold_stress":
            nums = (f"p90 series-mean/entry-quote {r['p90']:.2f} (median {r['median']:.2f}, n {r['n']})"
                    if r["p90"] is not None else "no series-mean/entry-quote ratio recorded")
            cond = "p90 > 1.0"
        else:
            nums = f"{r['arm']} series episodes: " + _arm_text(r)
            cond = "mean ≤ 0" + (" (thin arm, n < 10)" if 0 < r["n"] < 10 else "")
        mark = "HOLDS" if r["holds"] else "does not hold"
        lines.append(f"  ({r['rule']}) {cond}: {mark} — {nums}")
        if r["holds"]:
            holding.append(f"{r['env']}={r['value']}")
    if holding:
        lines.append("  implied env change (main session decides; nothing applied): " + " ".join(holding))
    else:
        lines.append("  no revisit rule holds: keep the current env.")
    return "\n".join(lines)


# ----------------------------------------------------------------------- I/O

_FILLS_SQL = (
    "SELECT p.strategy_id, p.asset_class, p.symbol, p.side, p.generated_at, p.horizon_seconds, "
    "       pp.notional_usd, pp.opened_at, pp.closed_at, pp.pnl_usd, "
    "       p.context->>'borrow_rate_hourly' AS borrow_rate_hourly, "
    "       p.context->>'borrow_charged_usd' AS borrow_charged_usd, "
    "       p.context->'book_close'->>'total_bps' AS book_close_bps, "
    "       p.context->>'borrow_source' AS borrow_source, "
    "       p.context->>'borrow_series_mean_hourly' AS borrow_series_mean_hourly, "
    "       p.context->'entry_filter'->>'flat_keep' AS flat_keep, "
    "       p.context->'entry_filter'->>'naive_keep' AS naive_keep, "
    "       p.context->'entry_filter'->>'decay_keep' AS decay_keep "
    "FROM paper_positions pp JOIN predictions p ON p.id = pp.prediction_id "
    "WHERE p.strategy_id = :sid AND p.asset_class = :ac AND p.generated_at >= :since "
    "ORDER BY p.generated_at"
)


async def load_bands() -> dict[tuple[str, str], dict[str, Any]]:
    """(strategy_id, asset_class) → band for every shadow/active config with
    one: its own `params.shadow_band`, else `DEFAULT_BANDS`."""
    from matrix_shared import shared_session_scope

    async with shared_session_scope() as s:
        rows = (await s.execute(text(
            "SELECT strategy_id, asset_class, params FROM strategy_configs "
            "WHERE status IN ('shadow', 'active') ORDER BY strategy_id, asset_class, version"
        ))).mappings().all()
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        key = (r["strategy_id"], r["asset_class"])
        band = (r["params"] or {}).get("shadow_band") or DEFAULT_BANDS.get(key)
        if band:
            out[key] = {**DEFAULT_BANDS.get(key, {}), **band}  # highest version wins
    return out


async def _qualifying(band: dict[str, Any], now: datetime) -> int | None:
    """Settlements on the band's watchlist at or below its funding threshold
    in the last `stale_hours`: the opportunities the strategy had."""
    if not band.get("qualify_universe") or band.get("qualify_funding_max") is None:
        return None
    from matrix_shared import local_session_scope, shared_session_scope

    async with shared_session_scope() as s:
        syms = [r[0] for r in (await s.execute(text(
            "SELECT symbol FROM tradable_symbols WHERE asset_class = :ac AND active"
        ), {"ac": band["qualify_universe"]})).all()]
    if not syms:
        return 0
    async with local_session_scope() as s:
        return int((await s.execute(text(
            "SELECT count(DISTINCT (symbol, next_funding_ts)) FROM market_ticker_snapshots "
            "WHERE symbol = ANY(:syms) AND exchange = 'bybit' AND snapshot_ts > :since "
            "AND next_funding_ts <= :now AND snapshot_ts < next_funding_ts "
            "AND snapshot_ts > next_funding_ts - interval '10 minutes' "
            "AND funding_rate <= :thr"
        ), {
            "syms": syms, "since": now - timedelta(hours=float(band.get("stale_hours", 72))),
            "now": now, "thr": float(band["qualify_funding_max"]),
        })).scalar() or 0)


async def collect(now: datetime | None = None) -> list[dict[str, Any]]:
    """One report per strategy with a registered band."""
    from matrix_shared import shared_session_scope

    now = now or datetime.now(UTC)
    reports = []
    for (sid, ac), band in sorted((await load_bands()).items()):
        since = _utc(datetime.fromisoformat(band["since"])) if band.get("since") else now - timedelta(days=90)
        async with shared_session_scope() as s:
            rows = [dict(r) for r in (await s.execute(
                text(_FILLS_SQL), {"sid": sid, "ac": ac, "since": since}
            )).mappings().all()]
        try:
            q = await _qualifying(band, now)
        except Exception as e:  # noqa: BLE001 — unknown opportunity count must not hide the rest
            logger.warning(f"shadow tracker: qualifying count for {sid} failed: {e}")
            q = None
        reports.append(evaluate(rows, band, now=now, qualifying=q, strategy_id=sid, asset_class=ac))
    return reports

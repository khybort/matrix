"""Deterministic system digest — what the Director reads every tick.

No LLM here. Every number comes from the shared/local tiers so the digest
is identical whether the LLM path is up or not; the agent layer only decides
what to *do* about it. Also rendered as a plain-text brief for Telegram/logs.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger
from matrix_shared import local_session_scope, shared_session_scope
from matrix_shared.edge_study import episode_groups, episode_pnls
from sqlalchemy import text

# Realised fills with what episode grouping needs. Rows are NOT samples: a
# strategy that re-emits the same (symbol, side) inside its horizon and gets
# filled again has made one bet (docs/wiki/edge-study.md), so every n and win
# rate below is counted in episodes (`edge_study.episode_groups`), with the raw
# fill count beside it as n_raw and excluded flat-closes as n_unscorable.
FILLS_SQL = (
    "SELECT p.strategy_id, p.asset_class, p.strategy_version AS version, p.symbol, p.side, "
    "       p.generated_at, p.horizon_seconds, coalesce(p.context->>'method','(none)') AS method, "
    "       o.pnl_usd, o.observed_at, o.reason "
    "FROM outcomes o JOIN predictions p ON p.id = o.prediction_id "
)


def episode_summary(rows: list[dict], key: tuple[str, ...]) -> list[dict[str, Any]]:
    """Per-`key` realised PnL over episodes. Dollars of re-filled duplicates are
    summed into their episode (they were real positions); n, win rate and mean
    are per episode. Sorted by pnl ascending."""
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        groups.setdefault(tuple(r[k] for k in key), []).append(r)
    out = []
    for k, rs in groups.items():
        scorable = sorted((r for r in rs if r["reason"] != "orphan_flat_close"),
                          key=lambda r: r["generated_at"])
        bets = episode_pnls(scorable)
        n = len(bets)
        total = sum(bets)
        out.append({
            **dict(zip(key, k)), "n": n, "n_raw": len(scorable), "n_unscorable": len(rs) - len(scorable),
            "pnl": round(total, 2),
            "win_rate": round(sum(1 for b in bets if b > 0) / n, 3) if n else None,
            "avg_pnl": round(total / n, 4) if n else None,
        })
    return sorted(out, key=lambda r: r["pnl"])


def ranker_summary(rows: list[dict]) -> list[dict[str, Any]]:
    """Traded vs skipped signals per market, one value per episode. An episode
    is traded when any of its signals was filled (value: mean of its fills'
    pnl_pct); otherwise its first signal's virtual outcome is the bet."""
    by_ac: dict[str, dict[str, list[float]]] = {}
    for g in episode_groups(sorted(rows, key=lambda r: r["generated_at"])):
        acc = by_ac.setdefault(g[0]["asset_class"], {"traded": [], "untraded": [], "raw_t": [], "raw_u": []})
        filled = [float(r["pnl_pct"]) for r in g if r["pnl_pct"] is not None]
        acc["raw_t"].extend(filled)
        acc["raw_u"].extend(1.0 for r in g if r["virtual_pct"] is not None)
        if filled:
            acc["traded"].append(sum(filled) / len(filled))
        elif g[0]["virtual_pct"] is not None:
            acc["untraded"].append(float(g[0]["virtual_pct"]))

    def bps(xs: list[float]) -> float | None:
        return round(sum(xs) / len(xs) * 10_000, 2) if xs else None

    return [{
        "asset_class": ac, "n_traded": len(a["traded"]), "n_traded_raw": len(a["raw_t"]),
        "traded_bps": bps(a["traded"]), "n_untraded": len(a["untraded"]),
        "n_untraded_raw": len(a["raw_u"]), "untraded_bps": bps(a["untraded"]),
    } for ac, a in sorted(by_ac.items())]


@dataclass(slots=True)
class SystemDigest:
    now: datetime
    health: dict[str, Any] = field(default_factory=dict)
    pnl: list[dict[str, Any]] = field(default_factory=list)          # per strategy/market, 24h + 7d
    challengers: list[dict[str, Any]] = field(default_factory=list)  # active shadow configs
    efficacy: dict[str, Any] = field(default_factory=dict)
    proposals: dict[str, Any] = field(default_factory=dict)
    dev: dict[str, Any] = field(default_factory=dict)
    lessons: dict[str, Any] = field(default_factory=dict)
    certs: list[dict[str, Any]] = field(default_factory=list)
    wallets: list[dict[str, Any]] = field(default_factory=list)
    by_method: list[dict[str, Any]] = field(default_factory=list)  # matrix_agent PnL by decision method
    regime: dict[str, Any] = field(default_factory=dict)  # asset_class → regime key
    ranker: list[dict[str, Any]] = field(default_factory=list)  # traded vs untraded (virtual) pnl% per market
    llm: dict[str, Any] = field(default_factory=dict)  # today's LLM usage by service (usage_ledger)

    def as_dict(self) -> dict[str, Any]:
        return {
            "now": self.now.isoformat(), "health": self.health, "pnl": self.pnl,
            "challengers": self.challengers, "efficacy": self.efficacy, "proposals": self.proposals,
            "dev": self.dev, "lessons": self.lessons, "certs": self.certs, "wallets": self.wallets,
            "by_method": self.by_method, "regime": self.regime, "ranker": self.ranker, "llm": self.llm,
        }


def _age(now: datetime, ts: datetime | None) -> float | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return round(max(0.0, (now - ts).total_seconds()), 1)  # exchange clocks can run ahead


async def collect_digest(now: datetime | None = None) -> SystemDigest:
    now = now or datetime.now(UTC)
    d = SystemDigest(now=now)

    try:
        async with shared_session_scope() as s:
            d.health["paper_snapshot_age_s"] = _age(now, (await s.execute(text(
                "SELECT max(snapshot_ts) FROM wallet_snapshots"))).scalar())
            d.health["crypto_prediction_age_s"] = _age(now, (await s.execute(text(
                "SELECT max(created_at) FROM predictions WHERE asset_class='crypto'"))).scalar())
            row = (await s.execute(text(
                "SELECT count(*), count(*) FILTER (WHERE context->>'method' LIKE 'llm%') "
                "FROM predictions WHERE strategy_id='matrix_agent' AND context->>'method' IS NOT NULL "
                "AND created_at >= now() - interval '60 minutes'"))).one()
            d.health["agent_predictions_60m"] = int(row[0] or 0)
            d.health["agent_llm_predictions_60m"] = int(row[1] or 0)

            d.wallets = [dict(r) for r in (await s.execute(text(
                "SELECT name, asset_class, round(cash_usd+locked_usd,2) AS equity, "
                "round(cash_usd+locked_usd-starting_capital_usd,2) AS net_pnl, "
                "circuit_tripped_at IS NOT NULL AS circuit_tripped FROM wallets ORDER BY asset_class, name"
            ))).mappings().all()]

            fills = [dict(r) for r in (await s.execute(text(
                FILLS_SQL + "WHERE o.observed_at >= now()-interval '7 days' "
                "  AND coalesce(p.context->>'is_shadow','false') <> 'true'"))).mappings().all()]
            key = ("strategy_id", "asset_class", "version")
            day = {tuple(r[k] for k in key): r for r in episode_summary(
                [f for f in fills if f["observed_at"] >= now - timedelta(hours=24)], key)}
            d.pnl = []
            for r in episode_summary(fills, key):
                r24 = day.get(tuple(r[k] for k in key), {})
                d.pnl.append({
                    "strategy_id": r["strategy_id"], "asset_class": r["asset_class"], "version": r["version"],
                    "n_24h": r24.get("n", 0), "pnl_24h": r24.get("pnl", 0.0),
                    "n_7d": r["n"], "n_raw_7d": r["n_raw"], "n_unscorable_7d": r["n_unscorable"],
                    "pnl_7d": r["pnl"], "win_rate_7d": r["win_rate"],
                })

            shadows = (await s.execute(text(
                "SELECT strategy_id, asset_class, version, promoted_at FROM strategy_configs "
                "WHERE status='shadow' ORDER BY 1,2"))).mappings().all()
            d.challengers = []
            for sc in shadows:
                rows = [dict(r) for r in (await s.execute(text(
                    FILLS_SQL + "WHERE p.strategy_id=:sid AND p.asset_class=:ac AND p.strategy_version=:v"),
                    {"sid": sc["strategy_id"], "ac": sc["asset_class"], "v": sc["version"]})).mappings().all()]
                summ = episode_summary(rows, ("strategy_id",))
                d.challengers.append({
                    **dict(sc), "n": summ[0]["n"] if summ else 0, "n_raw": summ[0]["n_raw"] if summ else 0,
                    "n_unscorable": summ[0]["n_unscorable"] if summ else 0,
                })

            eff = (await s.execute(text(
                "SELECT metrics_window->'efficacy'->>'verdict' AS verdict, count(*) "
                "FROM mutation_proposals WHERE updated_at >= now()-interval '7 days' "
                "  AND metrics_window->'efficacy' IS NOT NULL GROUP BY 1"))).all()
            d.efficacy = {"verdicts_7d": {str(v or "none"): int(n) for v, n in eff}}
            d.efficacy["rollbacks_7d"] = int((await s.execute(text(
                "SELECT count(*) FROM mutation_proposals WHERE proposal_type='rollback' "
                "AND created_at >= now()-interval '7 days'"))).scalar() or 0)
            d.efficacy["cutovers_7d"] = int((await s.execute(text(
                "SELECT count(*) FROM mutation_proposals WHERE proposal_type='cutover' "
                "AND created_at >= now()-interval '7 days'"))).scalar() or 0)
            d.efficacy["challengers_retired_7d"] = int((await s.execute(text(
                "SELECT count(*) FROM mutation_proposals WHERE proposal_type='challenger_retired' "
                "AND created_at >= now()-interval '7 days'"))).scalar() or 0)

            d.proposals = {f"{st}/{pt}": int(n) for st, pt, n in (await s.execute(text(
                "SELECT status, proposal_type, count(*) FROM mutation_proposals "
                "WHERE created_at >= now()-interval '7 days' GROUP BY 1,2 ORDER BY 1,2"))).all()}

            d.by_method = [
                {k: r[k] for k in ("method", "n", "n_raw", "n_unscorable", "pnl", "win_rate")}
                for r in episode_summary([f for f in fills if f["strategy_id"] == "matrix_agent"], ("method",))
            ]

            d.ranker = ranker_summary([dict(r) for r in (await s.execute(text(
                "SELECT p.strategy_id, p.asset_class, p.symbol, p.side, p.generated_at, p.horizon_seconds, "
                "  o.pnl_pct, (p.context->'virtual_outcome'->>'pnl_pct')::numeric AS virtual_pct "
                "FROM predictions p LEFT JOIN outcomes o ON o.prediction_id = p.id AND o.reason <> 'orphan_flat_close' "
                "WHERE p.created_at >= now()-interval '7 days' AND p.side IN ('long','short') "
                "  AND coalesce(p.context->>'is_shadow','false') <> 'true'"))).mappings().all()])

            d.lessons = {f"{ac}/{v}": int(n) for ac, v, n in (await s.execute(text(
                "SELECT asset_class, verdict, count(*) FROM agent_lessons WHERE status='active' "
                "GROUP BY 1,2 ORDER BY 1,2"))).all()}

            d.certs = [dict(r) for r in (await s.execute(text(
                "SELECT strategy_id, asset_class, version, status, granted_by, validity_until "
                "FROM paper_trade_certificate WHERE status='granted' ORDER BY 1,2"))).mappings().all()]
    except Exception as e:  # noqa: BLE001 — a partial digest beats no digest
        logger.warning(f"digest: shared-tier probe failed: {e}")

    try:
        async with local_session_scope() as s:
            d.health["crypto_tick_age_s"] = _age(now, (await s.execute(text(
                "SELECT snapshot_ts FROM market_ticker_snapshots WHERE symbol=:sym "
                "ORDER BY snapshot_ts DESC LIMIT 1"),
                {"sym": os.environ.get("MATRIX_HEALTH_PROBE_SYMBOL", "BTCUSDT")})).scalar())
            d.health["crypto_bar_age_s"] = _age(now, (await s.execute(text(
                "SELECT ts FROM market_bars WHERE asset_class='crypto' AND interval='1m' "
                "ORDER BY ts DESC LIMIT 1"))).scalar())
            d.health["local_db_gb"] = round(float((await s.execute(text(
                "SELECT pg_database_size(current_database())"))).scalar() or 0) / 1e9, 1)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"digest: local-tier probe failed: {e}")
    try:
        from matrix_shared.regime import current_regime

        for ac in ("crypto", "bist", "us"):
            r = await current_regime(ac)
            if r.key != "unknown/unknown/unknown":
                d.regime[ac] = r.as_dict()
    except Exception as e:  # noqa: BLE001
        logger.debug(f"digest: regime probe failed ({e})")
    try:
        u = shutil.disk_usage("/")
        d.health["disk_free_pct"] = round(u.free / u.total * 100, 1)
    except OSError:
        pass
    # dev_agent queue lives on the LOCAL tier (dev_agent writes LOCAL_DATABASE_URL);
    # reading the empty SHARED copy made the digest report a permanently idle dev_agent.
    try:
        async with local_session_scope() as s:
            d.dev = {
                "by_status_7d": {str(st): int(n) for st, n in (await s.execute(text(
                    "SELECT status::text, count(*) FROM dev_tasks "
                    "WHERE created_at >= now()-interval '7 days' GROUP BY 1"))).all()},
                "pending": int((await s.execute(text(
                    "SELECT count(*) FROM dev_tasks WHERE status='pending'"))).scalar() or 0),
                "spend_today_usd": float((await s.execute(text(
                    "SELECT coalesce(sum(total_cost_usd),0) FROM dev_tasks "
                    "WHERE finished_at >= date_trunc('day', now())"))).scalar() or 0),
                "recent_failures": [dict(r) for r in (await s.execute(text(
                    "SELECT id, failure_reason, left(description, 90) AS description "
                    "FROM dev_tasks WHERE status='failed' AND finished_at >= now()-interval '24 hours' "
                    "ORDER BY finished_at DESC LIMIT 5"))).mappings().all()],
            }
    except Exception as e:  # noqa: BLE001 — digest must never fail the tick
        logger.warning(f"digest: dev_tasks probe failed: {e}")

    # LLM spend / turns today, per service, from the shared usage ledger.
    try:
        from matrix_shared.usage_ledger import summary as _usage_summary
        d.llm = _usage_summary(days=1)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"digest: usage ledger unavailable ({e})")

    return d


def render_brief(d: SystemDigest) -> str:
    """Compact operator brief (Telegram-sized)."""
    h = d.health
    lines = [f"🧭 Director brief {d.now.strftime('%Y-%m-%d %H:%M')} UTC"]
    lines.append(
        "health: paper {}s · signals {}s · ticks {}s · bars {}s · llm {}/{} · disk {}% free · db {}GB".format(
            h.get("paper_snapshot_age_s", "?"), h.get("crypto_prediction_age_s", "?"),
            h.get("crypto_tick_age_s", "?"), h.get("crypto_bar_age_s", "?"),
            h.get("agent_llm_predictions_60m", "?"), h.get("agent_predictions_60m", "?"),
            h.get("disk_free_pct", "?"), h.get("local_db_gb", "?"),
        )
    )
    if d.regime:
        lines.append("regime: " + ", ".join(f"{ac} {r['key']} (24h {r.get('ret_24h')})" for ac, r in d.regime.items()))
    for w in d.wallets:
        flag = " ⚠️circuit" if w.get("circuit_tripped") else ""
        lines.append(f"wallet {w['asset_class']}/{w['name']}: equity {w['equity']} net {w['net_pnl']}{flag}")
    if d.pnl:
        lines.append("pnl 7d (worst→best):")
        for r in d.pnl[:6]:
            lines.append(f"  {r['strategy_id']}/{r['asset_class']} v{r['version']}: "
                         f"{r['pnl_7d']} USD n={r['n_7d']} ep/{r.get('n_raw_7d', '?')} fills "
                         f"wr={r['win_rate_7d']} | 24h {r['pnl_24h']} (n={r['n_24h']})")
        if len(d.pnl) > 6:
            best = d.pnl[-1]
            lines.append(f"  … best {best['strategy_id']}/{best['asset_class']}: {best['pnl_7d']} USD")
    if d.challengers:
        lines.append("challengers: " + ", ".join(
            f"{c['strategy_id']}/{c['asset_class']} v{c['version']} (n={c['n']} ep/{c['n_raw']} fills)"
            for c in d.challengers))
    e = d.efficacy
    if d.llm.get("calls"):
        top = ", ".join(f"{svc} ${v['cost_usd']:.2f}/{v['calls']}" for svc, v in list(d.llm["by_service"].items())[:4])
        lines.append(f"llm today: ${d.llm['cost_usd']:.2f} over {d.llm['calls']} calls ({top})")
    lines.append(f"efficacy 7d: {e.get('verdicts_7d', {})} rollbacks={e.get('rollbacks_7d', 0)} "
                 f"cutovers={e.get('cutovers_7d', 0)} retired={e.get('challengers_retired_7d', 0)}")
    dv = d.dev
    lines.append(f"dev_agent 7d: {dv.get('by_status_7d', {})} pending={dv.get('pending', 0)} "
                 f"spend_today=${dv.get('spend_today_usd', 0):.2f}")
    for r in d.ranker:
        if r.get("n_untraded"):
            lines.append(f"ranker {r['asset_class']}: traded {r['traded_bps']} bps (n={r['n_traded']} ep) vs "
                         f"skipped-would-have {r['untraded_bps']} bps (n={r['n_untraded']} ep)")
    if d.by_method:
        lines.append("matrix_agent by method 7d: " + ", ".join(
            f"{m['method']} n={m['n']} ep/{m['n_raw']} fills pnl={m['pnl']} wr={m['win_rate']}"
            for m in d.by_method))
    if d.lessons:
        lines.append(f"lessons active: {d.lessons}")
    relaxed = sum(1 for c in d.certs if str(c.get('granted_by') or '').endswith('+relaxed'))
    lines.append(f"certs granted: {len(d.certs)} ({relaxed} relaxed/testnet-only)")
    return "\n".join(lines)

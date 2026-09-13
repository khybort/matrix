"""Deterministic system digest — what the Director reads every tick.

No LLM here. Every number comes from the shared/local tiers so the digest
is identical whether the LLM path is up or not; the agent layer only decides
what to *do* about it. Also rendered as a plain-text brief for Telegram/logs.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from loguru import logger
from matrix_shared import local_session_scope, shared_session_scope
from sqlalchemy import text


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

    def as_dict(self) -> dict[str, Any]:
        return {
            "now": self.now.isoformat(), "health": self.health, "pnl": self.pnl,
            "challengers": self.challengers, "efficacy": self.efficacy, "proposals": self.proposals,
            "dev": self.dev, "lessons": self.lessons, "certs": self.certs, "wallets": self.wallets,
        }


def _age(now: datetime, ts: datetime | None) -> float | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return round((now - ts).total_seconds(), 1)


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

            d.pnl = [dict(r) for r in (await s.execute(text(
                "SELECT p.strategy_id, p.asset_class, p.strategy_version AS version, "
                "       count(*) FILTER (WHERE o.observed_at >= now()-interval '24 hours') AS n_24h, "
                "       round(coalesce(sum(o.pnl_usd) FILTER (WHERE o.observed_at >= now()-interval '24 hours'),0),2) AS pnl_24h, "
                "       count(*) AS n_7d, round(sum(o.pnl_usd),2) AS pnl_7d, "
                "       round(avg((o.pnl_usd>0)::int),3) AS win_rate_7d "
                "FROM outcomes o JOIN predictions p ON p.id=o.prediction_id "
                "WHERE o.observed_at >= now()-interval '7 days' AND o.reason <> 'orphan_flat_close' "
                "  AND coalesce(p.context->>'is_shadow','false') <> 'true' "
                "GROUP BY 1,2,3 ORDER BY pnl_7d"))).mappings().all()]

            d.challengers = [dict(r) for r in (await s.execute(text(
                "SELECT sc.strategy_id, sc.asset_class, sc.version, sc.promoted_at, "
                "  (SELECT count(*) FROM outcomes o JOIN predictions p ON p.id=o.prediction_id "
                "    WHERE p.strategy_id=sc.strategy_id AND p.asset_class=sc.asset_class "
                "      AND p.strategy_version=sc.version) AS n_outcomes "
                "FROM strategy_configs sc WHERE sc.status='shadow' ORDER BY 1,2"))).mappings().all()]

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
        u = shutil.disk_usage("/")
        d.health["disk_free_pct"] = round(u.free / u.total * 100, 1)
    except OSError:
        pass
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
    for w in d.wallets:
        flag = " ⚠️circuit" if w.get("circuit_tripped") else ""
        lines.append(f"wallet {w['asset_class']}/{w['name']}: equity {w['equity']} net {w['net_pnl']}{flag}")
    if d.pnl:
        lines.append("pnl 7d (worst→best):")
        for r in d.pnl[:6]:
            lines.append(f"  {r['strategy_id']}/{r['asset_class']} v{r['version']}: "
                         f"{r['pnl_7d']} USD n={r['n_7d']} wr={r['win_rate_7d']} | 24h {r['pnl_24h']} (n={r['n_24h']})")
        if len(d.pnl) > 6:
            best = d.pnl[-1]
            lines.append(f"  … best {best['strategy_id']}/{best['asset_class']}: {best['pnl_7d']} USD")
    if d.challengers:
        lines.append("challengers: " + ", ".join(
            f"{c['strategy_id']}/{c['asset_class']} v{c['version']} (n={c['n_outcomes']})" for c in d.challengers))
    e = d.efficacy
    lines.append(f"efficacy 7d: {e.get('verdicts_7d', {})} rollbacks={e.get('rollbacks_7d', 0)} "
                 f"cutovers={e.get('cutovers_7d', 0)} retired={e.get('challengers_retired_7d', 0)}")
    dv = d.dev
    lines.append(f"dev_agent 7d: {dv.get('by_status_7d', {})} pending={dv.get('pending', 0)} "
                 f"spend_today=${dv.get('spend_today_usd', 0):.2f}")
    if d.lessons:
        lines.append(f"lessons active: {d.lessons}")
    relaxed = sum(1 for c in d.certs if str(c.get('granted_by') or '').endswith('+relaxed'))
    lines.append(f"certs granted: {len(d.certs)} ({relaxed} relaxed/testnet-only)")
    return "\n".join(lines)

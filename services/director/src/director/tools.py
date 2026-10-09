"""Director tool belt.

read        system_digest, strategy_pnl, efficacy_report, dev_tasks_report, active_lessons
write       file_dev_task, retire_strategy
risk-gated  revoke_certificate — only moves in the SAFE direction (revokes);
            nothing here can grant a cert, change a risk cap, enable live
            execution or touch a wallet. Those stay human/code-enforced
            (docs/TRADING.md).
"""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime
from typing import Any

import orjson
from loguru import logger
from matrix_shared import local_session_scope, shared_session_scope
from matrix_shared.agent_runtime.tool import ToolRegistry, tool
from sqlalchemy import text

from director.digest import FILLS_SQL, SystemDigest, collect_digest, episode_summary

_SERVER = "director"
MAX_DEV_TASKS_PER_TICK = int(os.environ.get("DIRECTOR_MAX_DEV_TASKS_PER_TICK", "2"))
DEV_TASK_DEDUP_DAYS = int(os.environ.get("DIRECTOR_DEV_TASK_DEDUP_DAYS", "7"))


def _text(payload: Any) -> dict:
    return {"content": [{"type": "text", "text": orjson.dumps(payload, default=str).decode()}]}


def _error(msg: str) -> dict:
    return {"content": [{"type": "text", "text": f"ERROR: {msg}"}], "is_error": True}


class TickState:
    """Per-tick bookkeeping so the model can't spam writes in one loop."""

    def __init__(self, digest: SystemDigest) -> None:
        self.digest = digest
        self.dev_tasks_filed: list[int] = []
        self.actions: list[dict[str, Any]] = []


async def file_dev_task_impl(
    description: str, *, touches_files: list[str], priority: int = 2, source: str = "reflection",
) -> int | None:
    """Insert one dev_tasks row, deduped by a content marker for DEV_TASK_DEDUP_DAYS."""
    digest_key = hashlib.sha1(description.strip().lower().encode()).hexdigest()[:10]
    marker = f"[director:{digest_key}]"
    # dev_tasks lives on the LOCAL tier (dev_agent reads LOCAL_DATABASE_URL);
    # the SHARED copy is an empty migration artefact — a row written there is
    # never picked up (task #11 on 2026-09-13 was stranded that way).
    async with local_session_scope() as s:
        dup = (await s.execute(text(
            "SELECT id FROM dev_tasks WHERE description LIKE :m "
            "AND created_at >= now() - make_interval(days => :d) LIMIT 1"
        ), {"m": f"%{marker}%", "d": DEV_TASK_DEDUP_DAYS})).scalar()
        if dup is not None:
            return None
        task_id = (await s.execute(text(
            "INSERT INTO dev_tasks (source, description, priority, touches_files, run_tests, review_mode, auto_commit) "
            "VALUES (:src, :d, :p, :tf, TRUE, 'auto', FALSE) RETURNING id"
        ), {"src": source, "d": f"{marker} {description.strip()}", "p": int(priority),
            "tf": list(touches_files or [])})).scalar()
    return int(task_id) if task_id is not None else None


def build_registry(state: TickState) -> ToolRegistry:
    reg = ToolRegistry()

    @tool("system_digest",
          "The full deterministic digest for this tick: health ages, wallets, per-strategy PnL "
          "(24h/7d), active challengers, efficacy verdicts, proposal counts, dev_agent status, "
          "active lessons, certificates. Call this first.", {})
    async def system_digest(_args: dict) -> dict:
        return _text(state.digest.as_dict())

    @tool("strategy_pnl",
          "Realised PnL per (strategy, market, version) over the last N days, champion rows only. "
          "n, win_rate and avg_pnl are per episode (re-fills of one bet count once); n_raw is fills.",
          {"days": int})
    async def strategy_pnl(args: dict) -> dict:
        days = max(1, min(90, int(args.get("days", 7))))
        try:
            async with shared_session_scope() as s:
                fills = [dict(r) for r in (await s.execute(text(
                    FILLS_SQL + "WHERE o.observed_at >= now() - make_interval(days => :d) "
                    "  AND coalesce(p.context->>'is_shadow','false') <> 'true'"), {"d": days})).mappings().all()]
        except Exception as e:  # noqa: BLE001
            return _error(str(e))
        # n / win_rate / avg_pnl per episode (one bet, one sample); n_raw = fills.
        return _text(episode_summary(fills, ("strategy_id", "asset_class", "version")))

    @tool("efficacy_report",
          "Recent mutation efficacy: applied proposals with their before/after verdict, rollbacks, "
          "challenger cutovers/retirements (last N days).", {"days": int})
    async def efficacy_report(args: dict) -> dict:
        days = max(1, min(60, int(args.get("days", 7))))
        try:
            async with shared_session_scope() as s:
                rows = (await s.execute(text(
                    "SELECT strategy_id, asset_class, proposal_type, status, source, "
                    "       metrics_window->'efficacy' AS efficacy, metrics_window->'challenger' AS challenger, "
                    "       left(rationale, 160) AS rationale, updated_at "
                    "FROM mutation_proposals WHERE updated_at >= now() - make_interval(days => :d) "
                    "  AND (metrics_window->'efficacy' IS NOT NULL OR proposal_type IN ('rollback','cutover','challenger_retired')) "
                    "ORDER BY updated_at DESC LIMIT 40"), {"d": days})).mappings().all()
        except Exception as e:  # noqa: BLE001
            return _error(str(e))
        return _text([dict(r) for r in rows])

    @tool("quant_research",
          "EXPENSIVE (10-60s), call at most once per tick and only when you intend to act on it. "
          "Runs one of the controlled studies over the recent window and returns its rows. "
          "kind='edge': does each strategy's entry timing beat random entry on the same symbols, "
          "sides and barriers (Benjamini-Hochberg corrected across strategies)? "
          "kind='barrier': where do the take-profits sit in units of horizon volatility, and which "
          "multiple would have maximised net PnL? kind='meta': would a second model that decides "
          "whether to act on each signal add anything out of sample? "
          "Use these to judge whether a losing strategy lacks signal (retire it) or is losing to "
          "geometry/execution (file a dev task).",
          {"kind": str, "days": float})
    async def quant_research(args: dict) -> dict:
        kind = str(args.get("kind", "edge")).strip().lower()
        days = float(args.get("days") or 14.0)
        try:
            if kind == "edge":
                from matrix_shared.edge_study import run_edge_study

                rows = await run_edge_study(days=days)
            elif kind == "barrier":
                from matrix_shared.barrier_study import run_barrier_study

                rows = await run_barrier_study(days=days)
            elif kind == "meta":
                from matrix_shared.meta_label import run_meta_study

                rows = await run_meta_study(days=days)
            else:
                return _error(f"unknown kind {kind!r}; use edge | barrier | meta")
        except Exception as e:  # noqa: BLE001
            return _error(f"{kind} study failed: {e}")
        return _text({"kind": kind, "days": days, "rows": rows})

    @tool("dev_tasks_report",
          "dev_agent queue: pending / running / awaiting_review / recent failed tasks with reasons.", {})
    async def dev_tasks_report(_args: dict) -> dict:
        try:
            async with local_session_scope() as s:
                rows = (await s.execute(text(
                    "SELECT id, status::text, source::text, failure_reason, left(description, 140) AS description, "
                    "       review_notes, created_at, finished_at FROM dev_tasks "
                    "WHERE status IN ('pending','running','awaiting_review') "
                    "   OR (status <> 'discarded' AND finished_at >= now() - interval '48 hours') "
                    "ORDER BY created_at DESC LIMIT 30"))).mappings().all()
        except Exception as e:  # noqa: BLE001
            return _error(str(e))
        return _text([dict(r) for r in rows])

    @tool("active_lessons", "Active agent_lessons (avoid/prefer) per market, top 30 by confidence.", {})
    async def active_lessons(_args: dict) -> dict:
        try:
            async with shared_session_scope() as s:
                rows = (await s.execute(text(
                    "SELECT strategy_id, asset_class, verdict, pattern_description, confidence, "
                    "n_observations, win_rate, total_pnl_usd FROM agent_lessons WHERE status='active' "
                    "ORDER BY confidence DESC NULLS LAST LIMIT 30"))).mappings().all()
        except Exception as e:  # noqa: BLE001
            return _error(str(e))
        return _text([dict(r) for r in rows])

    @tool("file_dev_task",
          "File a coding task for dev_agent (source=reflection). Use for STRUCTURAL problems that "
          "parameter tuning cannot fix: a strategy losing across versions, a stalled pipeline, a "
          "recurring dev failure. Be specific: what to read, what to change, how to test. Deduped "
          f"for {DEV_TASK_DEDUP_DAYS} days; at most {MAX_DEV_TASKS_PER_TICK} per tick.",
          {"description": str, "touches_files": list, "priority": int}, side_effect="write")
    async def file_dev_task(args: dict) -> dict:
        if len(state.dev_tasks_filed) >= MAX_DEV_TASKS_PER_TICK:
            return _error("per-tick dev task budget exhausted")
        desc = str(args.get("description", "")).strip()
        if len(desc) < 40:
            return _error("description too short — say what to read, change and test")
        try:
            tid = await file_dev_task_impl(
                desc, touches_files=[str(x) for x in (args.get("touches_files") or [])],
                priority=int(args.get("priority", 2)),
            )
        except Exception as e:  # noqa: BLE001
            return _error(str(e))
        if tid is None:
            return _text({"filed": False, "reason": "duplicate within dedup window"})
        state.dev_tasks_filed.append(tid)
        state.actions.append({"action": "file_dev_task", "task_id": tid, "description": desc[:120]})
        logger.warning(f"director: filed dev task #{tid}: {desc[:100]}")
        return _text({"filed": True, "task_id": tid})

    @tool("retire_strategy",
          "Retire the ACTIVE config of a strategy in one market (status→retired). The strategy stops "
          "emitting signals; open positions still close normally. Use only for a persistent loser "
          "(negative 7d AND 24h PnL, n≥100, no pending challenger) — reflection/labs can revive it "
          "later with a new proposal. Never affects risk caps or live gates.",
          {"strategy_id": str, "asset_class": str, "reason": str}, side_effect="write")
    async def retire_strategy(args: dict) -> dict:
        sid, ac = str(args.get("strategy_id", "")), str(args.get("asset_class", ""))
        reason = str(args.get("reason", ""))[:300]
        if not sid or not ac or len(reason) < 20:
            return _error("strategy_id, asset_class and a real reason are required")
        try:
            async with shared_session_scope() as s:
                n = (await s.execute(text(
                    "UPDATE strategy_configs SET status='retired', updated_at=now(), "
                    "rationale = coalesce(rationale,'') || ' [director retire ' || :ts || ': ' || :r || ']' "
                    "WHERE strategy_id=:sid AND asset_class=:ac AND status='active' RETURNING version"
                ), {"sid": sid, "ac": ac, "r": reason, "ts": datetime.now(UTC).date().isoformat()})).all()
        except Exception as e:  # noqa: BLE001
            return _error(str(e))
        if not n:
            return _text({"retired": False, "reason": "no active config"})
        state.actions.append({"action": "retire_strategy", "strategy_id": sid, "asset_class": ac,
                              "versions": [r[0] for r in n], "reason": reason})
        logger.warning(f"director: retired {sid}/{ac} v{[r[0] for r in n]}: {reason}")
        return _text({"retired": True, "versions": [r[0] for r in n]})

    @tool("revoke_certificate",
          "Revoke a granted paper_trade_certificate (status→revoked). SAFE direction only: closes the "
          "live-execution door for that strategy version. Use when a certified strategy's live-window "
          "PnL turned negative or its drawdown breached. Re-granting is automatic once metrics recover.",
          {"strategy_id": str, "asset_class": str, "version": int, "reason": str},
          side_effect="risk-gated")
    async def revoke_certificate(args: dict) -> dict:
        sid, ac = str(args.get("strategy_id", "")), str(args.get("asset_class", ""))
        try:
            ver = int(args.get("version"))
        except (TypeError, ValueError):
            return _error("version must be an int")
        reason = str(args.get("reason", ""))[:300]
        if not sid or not ac or len(reason) < 20:
            return _error("strategy_id, asset_class, version and a real reason are required")
        try:
            async with shared_session_scope() as s:
                n = (await s.execute(text(
                    "UPDATE paper_trade_certificate SET status='revoked', revoked_at=now(), "
                    "revoked_reason=:r, updated_at=now() "
                    "WHERE strategy_id=:sid AND asset_class=:ac AND version=:v AND status='granted' RETURNING id"
                ), {"sid": sid, "ac": ac, "v": ver, "r": f"director: {reason}"})).all()
        except Exception as e:  # noqa: BLE001
            return _error(str(e))
        if not n:
            return _text({"revoked": False, "reason": "no granted cert for that version"})
        state.actions.append({"action": "revoke_certificate", "strategy_id": sid, "asset_class": ac,
                              "version": ver, "reason": reason})
        logger.warning(f"director: revoked cert {sid}/{ac}/v{ver}: {reason}")
        return _text({"revoked": True})

    for t in (system_digest, strategy_pnl, efficacy_report, dev_tasks_report, active_lessons,
              quant_research,
              file_dev_task, retire_strategy, revoke_certificate):
        reg.add(t)
    return reg


__all__ = ["TickState", "build_registry", "collect_digest", "file_dev_task_impl", "_SERVER"]

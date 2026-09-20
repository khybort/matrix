"""Director agent — the one process that looks at the whole system and decides.

Hourly: build the deterministic digest → (if an LLM backend is up) run one
Sonnet tool loop with the Director belt → always emit the brief. Without an
LLM the tick degrades to brief + rule-based escalations, never to silence.
"""

from __future__ import annotations

import os
from typing import Any

from loguru import logger
from matrix_shared.agent_runtime.ratelimit import get_rate_limiter
from matrix_shared.agent_runtime.tool import build_sdk_mcp_server, mcp_tool_names
from matrix_shared.subscription_llm import MODEL_SONNET, call_subscription_agent, subscription_enabled

from director.digest import SystemDigest, render_brief
from director.tools import _SERVER, TickState, build_registry, file_dev_task_impl

MAX_TURNS = int(os.environ.get("DIRECTOR_MAX_TURNS", "10"))

SYSTEM_PROMPT = (
    "You are Matrix Director — the orchestrator of an autonomous paper-trading research system "
    "whose only goal is sustainable profit. Once an hour you read the system digest and decide "
    "what, if anything, must change. You do NOT trade and cannot touch risk caps, wallets, live "
    "flags or grant certificates; those are code-enforced.\n\n"
    "Procedure:\n"
    "1. Call `system_digest`. Use `strategy_pnl` / `efficacy_report` / `dev_tasks_report` / "
    "`active_lessons` only when the digest raises a question.\n"
    "2. Decide. Prefer doing nothing over acting on noise. Act only on evidence with n >= 100 "
    "outcomes or a clear structural signal (stalled pipeline, recurring dev failure, pipeline "
    "component silent).\n"
    "   - `file_dev_task` for structural problems parameter tuning cannot fix. Write it like a "
    "senior engineer's ticket: what to read, what is wrong, what to change, how to verify. "
    "Before filing about a failed dev task, check `dev_tasks_report`: rows marked `discarded`, "
    "reason `stale_heartbeat`, or whose review_notes attribute the failure to an incident are NOT "
    "evidence of a bug; a task already filed for the same problem (open, failed or discarded) means "
    "do not file again — re-file only with new evidence.\n"
    "   - `retire_strategy` for a persistent loser (negative 7d and 24h, n >= 100, no pending "
    "challenger) that already had mutations rolled back.\n"
    "   - `revoke_certificate` when a certified version is losing money now.\n"
    "   - `quant_research` when a strategy's PnL alone cannot tell you WHY it loses. A losing "
    "strategy whose entries beat random entry is losing to costs, geometry or execution — fix "
    "those, do not retire it. One whose entries do not beat random has no signal, and no amount "
    "of tuning will help. Prefer `edge`; use `barrier` when time exits dominate, `meta` when you "
    "suspect the problem is which signals get filled rather than the signals themselves. "
    "Statistical honesty: a result that does not survive the report's own multiple-testing "
    "correction is not a result.\n"
    "3. Finish with a plain-text brief (<= 12 lines) for the operator: state of the system, what "
    "you did and why, what you are watching. No JSON, no markdown tables.\n"
)


async def run_director_tick(digest: SystemDigest) -> dict[str, Any]:
    """Returns {'brief': str, 'actions': [...], 'mode': 'llm'|'rules'}."""
    state = TickState(digest)
    brief = render_brief(digest)

    if not subscription_enabled():
        actions = await rule_based_actions(digest, state)
        return {"brief": brief + ("\n\nactions (rules): " + str(actions) if actions else "\n\n(LLM path down — rules-only tick)"),
                "actions": actions, "mode": "rules"}

    registry = build_registry(state)
    server = build_sdk_mcp_server(_SERVER, registry)
    allowed = mcp_tool_names(_SERVER, registry)
    allowed_set = frozenset(allowed)

    def _deny(name: str, _params: dict) -> bool:
        return name in allowed_set

    parts: list[str] = []
    try:
        async for ev in call_subscription_agent(
            prompt="Run this hour's Director review. Start with `system_digest`.",
            system=SYSTEM_PROMPT,
            model=MODEL_SONNET,
            mcp_servers={_SERVER: server},
            allowed_tools=allowed,
            disallowed_tools=["Bash", "Write", "Edit", "NotebookEdit", "WebFetch", "WebSearch"],
            can_use_tool=_deny,
            max_turns=MAX_TURNS,
            session_id=_SERVER,
            limiter=get_rate_limiter(),
            tool_registry=registry,
            mcp_server_name=_SERVER,
        ):
            if ev.type == "assistant_text":
                parts.append(ev.payload.get("text", ""))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"director agent loop failed: {e}")

    llm_brief = "".join(parts).strip()
    if not llm_brief:
        actions = await rule_based_actions(digest, state)
        return {"brief": brief + "\n\n(LLM returned nothing — rules-only tick)", "actions": actions, "mode": "rules"}
    return {"brief": brief + "\n\n" + llm_brief, "actions": state.actions, "mode": "llm"}


async def rule_based_actions(digest: SystemDigest, state: TickState) -> list[dict[str, Any]]:
    """Fallback policy when no LLM: only the unambiguous, structural escalations."""
    h = digest.health
    actions: list[dict[str, Any]] = []

    def _stale(key: str, limit: float) -> bool:
        v = h.get(key)
        return v is not None and v > limit

    if _stale("paper_snapshot_age_s", 1800):
        tid = await file_dev_task_impl(
            "Paper engine has produced no wallet snapshot for over 30 minutes while the stack is up. "
            "Read services/backtest/src/backtest/main.py and paper_trade.py, find why the tick loop "
            "stalls (long query, lock, exception loop), add a guard/timeout and a test.",
            touches_files=["services/backtest/src/backtest/"], priority=5)
        if tid:
            actions.append({"action": "file_dev_task", "task_id": tid, "why": "paper engine stalled"})
    if _stale("crypto_bar_age_s", 3600):
        tid = await file_dev_task_impl(
            "Crypto 1m bars are over an hour stale while ticks are fresh. Read services/ingestion/src/"
            "ingestion/bars.py: the startup backfill_all() full-scans market_trades and blocks the loop; "
            "bound it (time window / batch) so the incremental tick runs within 60s. Add a test.",
            touches_files=["services/ingestion/src/ingestion/bars.py"], priority=4)
        if tid:
            actions.append({"action": "file_dev_task", "task_id": tid, "why": "bars stalled"})
    fails = digest.dev.get("recent_failures") or []
    reasons = [f.get("failure_reason") for f in fails]
    for r in set(reasons):
        if r and reasons.count(r) >= 3:
            tid = await file_dev_task_impl(
                f"dev_agent tasks keep failing with failure_reason='{r}' ({reasons.count(r)} in 24h). "
                "Read services/dev_agent/src/dev_agent/worker.py, sdk_runner.py and the failed tasks' "
                "dev_task_events; fix the systematic cause (prompt, timeout, tool policy) with a test.",
                touches_files=["services/dev_agent/src/dev_agent/"], priority=4)
            if tid:
                actions.append({"action": "file_dev_task", "task_id": tid, "why": f"recurring {r}"})
    state.actions.extend(actions)
    return actions

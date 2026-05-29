"""Mutation-proposal agent — third strangler-fig conversion.

The model gets a read-only tool belt over recent outcomes, the strategy's
existing predictions, the agent_lessons it already learned, and peer
strategies' configs, so the proposal is grounded in data rather than the
single-shot prompt's metric summary alone.

Risk-cap stripping lives in `reflection.parsing.parse_mutation_draft` —
both this agent path and the legacy `llm_propose` route through it.
"""

from __future__ import annotations

from typing import Any

import orjson
from loguru import logger
from matrix_shared import shared_session_scope
from matrix_shared.agent_runtime.ratelimit import get_rate_limiter
from matrix_shared.agent_runtime.tool import (
    ToolRegistry,
    build_sdk_mcp_server,
    mcp_tool_names,
    tool,
)
from matrix_shared.subscription_llm import (
    MODEL_SONNET,
    call_subscription_agent,
    subscription_enabled,
)
from sqlalchemy import desc, select, text

from reflection.metrics import StrategyMetrics
from reflection.parsing import (
    FORBIDDEN_RISK_FIELDS,
    MutationDraft,
    extract_json_object,
    parse_mutation_draft,
)

MAX_TURNS = 6
_SERVER = "reflection"


def _text(payload: Any) -> dict:
    return {"content": [{"type": "text", "text": orjson.dumps(payload, default=str).decode()}]}


def _error(msg: str) -> dict:
    return {"content": [{"type": "text", "text": f"ERROR: {msg}"}], "is_error": True}


def _build_registry(strategy_id: str) -> ToolRegistry:
    reg = ToolRegistry()

    @tool(
        "recent_outcomes",
        "Recent outcomes for this strategy. Default returns the most recent "
        "30 rows; if more exist, the response is summarized to top_15 + "
        "bottom_15 + per-symbol aggregates so the model sees shape without "
        "the full payload. Pass verbose=true (≤ 60 rows) only when you need "
        "the full list.",
        {"hours": float, "verbose": bool},
    )
    async def recent_outcomes(args: dict) -> dict:
        hours = float(args.get("hours", 24.0))
        verbose = bool(args.get("verbose", False))
        cap = 60 if verbose else 30
        sql = text(
            "SELECT o.score, o.pnl_usd, o.reason, p.symbol, p.side, p.confidence, "
            "       p.asset_class, p.created_at "
            "FROM outcomes o JOIN predictions p ON p.id = o.prediction_id "
            "WHERE p.strategy_id = :sid "
            "  AND p.created_at >= NOW() - (:hours || ' hours')::interval "
            "ORDER BY p.created_at DESC LIMIT :lim"
        )
        try:
            async with shared_session_scope() as session:
                rows = (await session.execute(
                    sql, {"sid": strategy_id, "hours": str(hours), "lim": cap}
                )).mappings().all()
        except Exception as e:
            return _error(f"query failed: {e}")
        items = [dict(r) for r in rows]
        if verbose or len(items) <= 30:
            return _text(items)
        # Compress: top 15 / bottom 15 + per-symbol aggregates.
        by_symbol: dict[str, dict] = {}
        for it in items:
            sym = str(it.get("symbol", ""))
            agg = by_symbol.setdefault(sym, {"n": 0, "wins": 0, "pnl": 0.0})
            agg["n"] += 1
            try:
                pnl = float(it.get("pnl_usd") or 0)
                agg["pnl"] += pnl
                if pnl > 0:
                    agg["wins"] += 1
            except (TypeError, ValueError):
                pass
        return _text({
            "head": items[:15],
            "tail": items[-15:],
            "total": len(items),
            "by_symbol": by_symbol,
        })

    @tool(
        "active_lessons",
        "Active agent_lessons for this strategy (avoid/prefer patterns "
        "synthesized from outcomes). Top 20 by confidence — align mutations "
        "with these; don't propose changes that contradict a high-confidence "
        "'avoid'.",
        {},
    )
    async def active_lessons(_args: dict) -> dict:
        sql = text(
            "SELECT pattern_kind, pattern_description, verdict, confidence, "
            "       n_observations, win_rate, total_pnl_usd "
            "FROM agent_lessons WHERE strategy_id = :sid AND status = 'active' "
            "ORDER BY confidence DESC NULLS LAST LIMIT 20"
        )
        try:
            async with shared_session_scope() as session:
                rows = (await session.execute(sql, {"sid": strategy_id})).mappings().all()
        except Exception as e:
            return _error(f"query failed: {e}")
        return _text([dict(r) for r in rows])

    @tool(
        "peer_strategies",
        "Other active strategies' params, for inspiration. Compare their "
        "weights / thresholds to the current strategy's.",
        {},
    )
    async def peer_strategies(_args: dict) -> dict:
        try:
            async with shared_session_scope() as session:
                from matrix_shared.models import StrategyConfig
                stmt = (
                    select(StrategyConfig)
                    .where(StrategyConfig.status == "active")
                    .where(StrategyConfig.strategy_id != strategy_id)
                    .order_by(desc(StrategyConfig.version))
                    .limit(10)
                )
                rows = (await session.execute(stmt)).scalars().all()
        except Exception as e:
            return _error(f"query failed: {e}")
        out = [
            {
                "strategy_id": r.strategy_id,
                "version": r.version,
                "asset_class": getattr(r, "asset_class", None),
                "params": r.params,
            }
            for r in rows
        ]
        return _text(out)

    for t in (recent_outcomes, active_lessons, peer_strategies):
        reg.add(t)
    return reg


_FORBIDDEN_LIST = ", ".join(sorted(FORBIDDEN_RISK_FIELDS))

# Module-level literal so the string is identical byte-for-byte across calls
# AND across process restarts (sorted() makes the forbidden list deterministic).
# A stable system_prompt is the only thing we can do at our layer to give the
# Claude Code CLI's internal prompt cache a chance to hit.
SYSTEM_PROMPT = (
    "You are Matrix Reflection — propose ONE conservative parameter "
    "mutation that could improve a strategy's average score over the "
    "next window.\n"
    "Use the read-only tools to ground your proposal in actual outcomes, "
    "active lessons, and peer-strategy configs (don't speculate).\n"
    f"NEVER propose changes to risk caps: {_FORBIDDEN_LIST}. "
    "The reflection layer is forbidden from touching them; any such "
    "field will be stripped.\n"
    "Allowed proposal_type values: weight_tune | threshold_change | "
    "prompt_change.\n"
    "End your reply with ONLY this JSON object:\n"
    '{"proposal_type":"weight_tune","after_params":{"weights":{...}},'
    '"rationale":"<concise grounded reasoning>"}\n'
    "Return {} if no change is warranted."
)


async def run_reflection_agent(
    strategy_id: str, current_params: dict[str, Any], m: StrategyMetrics
) -> MutationDraft | None:
    if not subscription_enabled():
        return None
    registry = _build_registry(strategy_id)
    server = build_sdk_mcp_server(_SERVER, registry)
    allowed = mcp_tool_names(_SERVER, registry)
    allowed_set = frozenset(allowed)

    def _deny(name: str, _params: dict) -> bool:
        return name in allowed_set

    user = (
        f"Strategy: {strategy_id}\n"
        f"Current params: {orjson.dumps(current_params).decode()}\n"
        f"Window metrics ({m.n_outcomes} outcomes): "
        f"avg_score={m.avg_score} win_rate={m.win_rate} "
        f"total_pnl_usd={m.total_pnl_usd}.\n"
        "Investigate via tools, then emit the JSON object (or {} for no change)."
    )

    parts: list[str] = []
    try:
        async for ev in call_subscription_agent(
            prompt=user,
            system=SYSTEM_PROMPT,
            model=MODEL_SONNET,  # quality-pinned: unaffected by the Haiku default flip
            mcp_servers={_SERVER: server},
            allowed_tools=allowed,
            disallowed_tools=["Bash", "Write", "Edit", "NotebookEdit"],
            can_use_tool=_deny,
            max_turns=MAX_TURNS,
            session_id=_SERVER,
            limiter=get_rate_limiter(),
        ):
            if ev.type == "assistant_text":
                parts.append(ev.payload.get("text", ""))
    except Exception as e:
        logger.warning(f"reflection agent loop failed: {e}")
        return None

    text_out = "".join(parts).strip()
    if not text_out:
        return None
    parsed = extract_json_object(text_out)
    return parse_mutation_draft(parsed, current_params=current_params, source="agent")

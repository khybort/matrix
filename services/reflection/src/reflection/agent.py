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
from matrix_shared.edge_study import episode_groups
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
# Outcome rows read before collapsing to bets; re-emitting strategies leave
# many rows per bet, so the bet cap applies after grouping.
RAW_ROW_CAP = 2000


def episode_items(rows: list[dict]) -> list[dict]:
    """One item per bet, newest first: the bet's fields, summed pnl_usd, mean
    score and the number of rows (`fills`) it left."""
    out = []
    for g in episode_groups(sorted(rows, key=lambda r: r["generated_at"])):
        bet = g[0]
        scores = [float(r["score"]) for r in g if r["score"] is not None]
        out.append({
            "symbol": bet["symbol"], "side": bet["side"], "confidence": bet["confidence"],
            "asset_class": bet["asset_class"], "created_at": bet["created_at"],
            "reason": bet["reason"], "fills": len(g),
            "pnl_usd": sum(float(r["pnl_usd"] or 0) for r in g),
            "score": sum(scores) / len(scores) if scores else None,
        })
    out.reverse()
    return out


def _text(payload: Any) -> dict:
    return {"content": [{"type": "text", "text": orjson.dumps(payload, default=str).decode()}]}


def _error(msg: str) -> dict:
    return {"content": [{"type": "text", "text": f"ERROR: {msg}"}], "is_error": True}


def _build_registry(strategy_id: str, asset_class: str | None = None) -> ToolRegistry:
    reg = ToolRegistry()

    @tool(
        "recent_outcomes",
        "Recent outcomes for this strategy, one item per bet (episode): a "
        "signal re-emitted or re-filled inside its horizon is the same bet, so "
        "its fills are summed into one item (`fills` = rows). Default returns "
        "the most recent 30 bets; if more exist, the response is summarized to "
        "top_15 + bottom_15 + per-symbol aggregates. Pass verbose=true (≤ 60 "
        "bets) only when you need the full list.",
        {"hours": float, "verbose": bool},
    )
    async def recent_outcomes(args: dict) -> dict:
        hours = float(args.get("hours", 24.0))
        verbose = bool(args.get("verbose", False))
        cap = 60 if verbose else 30
        sql = text(
            "SELECT o.score, o.pnl_usd, o.reason, p.symbol, p.side, p.confidence, "
            "       p.asset_class, p.created_at, p.strategy_id, p.generated_at, p.horizon_seconds "
            "FROM outcomes o JOIN predictions p ON p.id = o.prediction_id "
            "WHERE p.strategy_id = :sid "
            "  AND (:ac IS NULL OR p.asset_class = :ac) "
            "  AND p.created_at >= NOW() - (:hours || ' hours')::interval "
            "ORDER BY p.created_at DESC LIMIT :lim"
        )
        try:
            async with shared_session_scope() as session:
                rows = (await session.execute(
                    sql, {"sid": strategy_id, "ac": asset_class, "hours": str(hours), "lim": RAW_ROW_CAP}
                )).mappings().all()
        except Exception as e:
            return _error(f"query failed: {e}")
        episodes = episode_items([dict(r) for r in rows])
        n_raw = len(rows)
        items = episodes[:cap]
        if verbose or len(episodes) <= 30:
            return _text({"bets": items, "total": len(episodes), "n_raw": n_raw})
        # Compress: top 15 / bottom 15 + per-symbol aggregates over every bet.
        by_symbol: dict[str, dict] = {}
        by_reason: dict[str, dict] = {}
        for it in episodes:
            agg = by_symbol.setdefault(str(it["symbol"]), {"n": 0, "wins": 0, "pnl": 0.0})
            r = by_reason.setdefault(str(it["reason"]), {"n": 0, "pnl": 0.0})
            agg["n"] += 1
            r["n"] += 1
            agg["pnl"] += it["pnl_usd"]
            r["pnl"] += it["pnl_usd"]
            if it["pnl_usd"] > 0:
                agg["wins"] += 1
        return _text({
            "head": items[:15],
            "tail": items[-15:],
            "total": len(episodes),
            "n_raw": n_raw,
            "by_symbol": by_symbol,
            "by_exit_reason": by_reason,
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
            "  AND (:ac IS NULL OR asset_class = :ac) "
            "ORDER BY confidence DESC NULLS LAST LIMIT 20"
        )
        try:
            async with shared_session_scope() as session:
                rows = (await session.execute(
                    sql, {"sid": strategy_id, "ac": asset_class}
                )).mappings().all()
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
    "mutation that could improve a strategy's realised total_pnl_usd (after "
    "fees and slippage) over the next window. Win rate and score are "
    "diagnostics, not the objective; a change that raises win rate but "
    "shrinks total PnL is a bad proposal. Read the exit-reason mix first: "
    "hit_horizon dominating at about -7 bps means the take-profit is not "
    "reachable within the horizon (extend horizon or lower tp); hit_sl far "
    "more frequent than hit_tp relative to the tp/sl ratio means the stop sits "
    "inside the noise band (widen sl or demand a stronger signal).\n"
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
    registry = _build_registry(strategy_id, getattr(m, "asset_class", None))
    server = build_sdk_mcp_server(_SERVER, registry)
    allowed = mcp_tool_names(_SERVER, registry)
    allowed_set = frozenset(allowed)

    def _deny(name: str, _params: dict) -> bool:
        return name in allowed_set

    user = (
        f"Strategy: {strategy_id}\n"
        f"Current params: {orjson.dumps(current_params).decode()}\n"
        f"Window metrics ({m.n_outcomes} bets from {m.n_raw} outcome rows; win_rate per bet): "
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
            tool_registry=registry,
            mcp_server_name=_SERVER,
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

"""Wraps the SDK query() call with safety hooks and event streaming.

For tests, the caller can substitute query_fn= (e.g. fake_query). In prod,
query_fn=claude_agent_sdk.query.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import asyncpg

from dev_agent.events import write_event
from dev_agent.safety import (
    CostCap,
    CostCapError,
    ForbiddenPathError,
    SafetyError,
    ToolLoopDetector,
    ToolLoopError,
    check_tool_call,
)


@dataclass
class RunResult:
    completed: bool
    failure_reason: str | None = None
    total_cost_usd: float = 0.0
    total_tokens: int = 0
    event_count: int = 0
    files_modified: list[str] = field(default_factory=list)


async def run_task_with_query(
    *,
    pool: asyncpg.Pool,
    task_id: int,
    run_id: int,
    cwd: Path,
    max_turns: int,
    cost_cap_usd: float,
    query_fn: Callable,
    scenario: Any | None = None,
    prompt: str = "",
    options: Any = None,
) -> RunResult:
    """Drive a Claude Agent SDK run; record events; enforce gates."""
    cost = CostCap(per_task_cap_usd=cost_cap_usd)
    loops = ToolLoopDetector(threshold=5)
    files_modified: set[str] = set()
    seq = 0
    turns = 0
    failure_reason: str | None = None

    def _can_use(tool_name: str, params: dict) -> bool:
        check_tool_call(tool_name, params)  # raises ForbiddenPathError
        loops.record(tool_name, params)     # raises ToolLoopError
        return True

    if scenario is not None:
        class _Opts:
            can_use_tool = staticmethod(_can_use)
        iterator = query_fn(prompt, _Opts(), scenario)
    elif options is None:
        # query_fn provided directly (e.g. lifecycle tests / prod worker).
        # Build a minimal options shim so safety hooks still fire.
        class _ProdOpts:
            can_use_tool = staticmethod(_can_use)
        iterator = query_fn(prompt, _ProdOpts())
    else:
        options.can_use_tool = _can_use
        iterator = query_fn(prompt=prompt, options=options)

    try:
        async for ev in iterator:
            # Cancel switch — checked between every event.
            cancel = await pool.fetchval(
                "SELECT cancel_requested FROM dev_tasks WHERE id=$1", task_id
            )
            if cancel:
                failure_reason = "user_killed"
                break

            ev_type = ev.type
            ev_payload = ev.payload

            if ev_type == "result":
                continue

            if ev_type == "tool_use":
                turns += 1
                if turns >= max_turns:
                    failure_reason = "max_turns"
                    break
                if ev_payload.get("name") in ("Edit", "Write"):
                    fp = ev_payload.get("params", {}).get("file_path")
                    if fp:
                        files_modified.add(fp)

            try:
                cost_delta = float(ev_payload.get("cost_usd") or 0)
                cost.add(cost_delta)
            except CostCapError:
                failure_reason = "cost_cap_task"
                break

            seq += 1
            await write_event(
                pool,
                task_id=task_id, run_id=run_id, seq=seq,
                event_type=ev_type, payload=ev_payload,
            )
    except ForbiddenPathError:
        failure_reason = "trading_path_violation"
    except ToolLoopError:
        failure_reason = "tool_loop"
    except SafetyError as e:
        failure_reason = e.reason

    return RunResult(
        completed=failure_reason is None,
        failure_reason=failure_reason,
        total_cost_usd=cost.total,
        event_count=seq,
        files_modified=sorted(files_modified),
    )

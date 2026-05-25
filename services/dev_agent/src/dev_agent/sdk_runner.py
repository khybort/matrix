"""Wraps the SDK query() call with safety hooks and event streaming.

For tests, the caller can substitute query_fn= (e.g. fake_query). In prod,
query_fn=claude_agent_sdk.query.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Callable

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


@dataclass
class _NormalizedEvent:
    """Internal event shape used by the iteration loop.

    Both the fake_sdk's FakeEvent and the real claude_agent_sdk's typed
    messages get normalized to this shape so the rest of the runner is
    agnostic to which transport produced the events.
    """

    type: str
    payload: dict


async def _prompt_stream(prompt: str, task_id: int) -> AsyncIterator[dict]:
    """Wrap a single prompt string as the streaming input the SDK expects
    when `can_use_tool` is set."""
    yield {
        "type": "user",
        "message": {"role": "user", "content": prompt},
        "parent_tool_use_id": None,
        "session_id": f"dev-agent-task-{task_id}",
    }


async def _adapt_real_sdk(iterator) -> AsyncIterator[_NormalizedEvent]:
    """Convert claude_agent_sdk message objects into normalized events.

    AssistantMessage → one assistant_text + one tool_use per content block
    UserMessage      → user (tool_result blocks become tool_result events)
    SystemMessage    → system
    ResultMessage    → result (carries total_cost_usd / total_tokens)
    """
    async for msg in iterator:
        cls = type(msg).__name__
        if cls == "AssistantMessage":
            for block in getattr(msg, "content", []):
                bcls = type(block).__name__
                if bcls == "TextBlock":
                    yield _NormalizedEvent(
                        type="assistant_text",
                        payload={"text": getattr(block, "text", "")},
                    )
                elif bcls == "ToolUseBlock":
                    yield _NormalizedEvent(
                        type="tool_use",
                        payload={
                            "name": getattr(block, "name", ""),
                            "params": getattr(block, "input", {}),
                            "id": getattr(block, "id", ""),
                        },
                    )
                elif bcls == "ThinkingBlock":
                    yield _NormalizedEvent(
                        type="thinking",
                        payload={"text": getattr(block, "thinking", "")},
                    )
        elif cls == "UserMessage":
            for block in getattr(msg, "content", []) or []:
                bcls = type(block).__name__
                if bcls == "ToolResultBlock":
                    yield _NormalizedEvent(
                        type="tool_result",
                        payload={
                            "tool_use_id": getattr(block, "tool_use_id", ""),
                            "content": str(getattr(block, "content", "")),
                            "is_error": getattr(block, "is_error", False),
                        },
                    )
        elif cls == "ResultMessage":
            yield _NormalizedEvent(
                type="result",
                payload={
                    "total_cost_usd": getattr(msg, "total_cost_usd", None),
                    "duration_ms": getattr(msg, "duration_ms", None),
                    "num_turns": getattr(msg, "num_turns", None),
                    "is_error": getattr(msg, "is_error", False),
                },
            )
        else:
            # System messages and anything we don't classify go through as 'system'.
            yield _NormalizedEvent(type="system", payload={"raw": cls})


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
    # Set by the can_use_tool wrapper when a safety gate denies a call —
    # the real SDK swallows our raised SafetyError into a PermissionResultDeny,
    # so we record the reason here and the iteration loop checks it each turn.
    deferred_violation: list[str] = []

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
        # Real claude_agent_sdk path. Two SDK requirements:
        #   1. can_use_tool needs streaming-mode prompt (AsyncIterable[dict]).
        #   2. can_use_tool must be async and return PermissionResultAllow|Deny.
        from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

        async def _wrapped_can_use(tool_name, input_params, _ctx):
            try:
                _can_use(tool_name, input_params)
            except SafetyError as e:
                # Record the violation for the iteration loop and tell the SDK
                # to abort. `interrupt=True` stops the agent from continuing.
                deferred_violation.append(e.reason)
                return PermissionResultDeny(message=str(e), interrupt=True)
            return PermissionResultAllow(updated_input=input_params)

        options.can_use_tool = _wrapped_can_use
        raw_iter = query_fn(
            prompt=_prompt_stream(prompt, task_id),
            options=options,
        )
        iterator = _adapt_real_sdk(raw_iter)

    try:
        async for ev in iterator:
            # Cancel switch — checked between every event.
            cancel = await pool.fetchval(
                "SELECT cancel_requested FROM dev_tasks WHERE id=$1", task_id
            )
            if cancel:
                failure_reason = "user_killed"
                break
            # Real-SDK safety: check if the can_use_tool wrapper recorded a
            # violation in a previous turn. We let the SDK finish flushing
            # buffered messages so events are preserved, then exit.
            if deferred_violation:
                failure_reason = deferred_violation[0]
                break

            ev_type = ev.type
            ev_payload = ev.payload

            if ev_type == "result":
                # Real-SDK final summary. Carries authoritative total_cost_usd.
                final_cost = ev_payload.get("total_cost_usd")
                if final_cost is not None:
                    cost.total = max(cost.total, float(final_cost))
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

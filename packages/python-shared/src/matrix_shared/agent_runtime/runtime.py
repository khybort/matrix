"""SDK tool-loop driver — streams normalized events from a Claude run.

This is the generalized, streaming version of the proven pattern in
`services/dev_agent/src/dev_agent/sdk_runner.py`. It drives the subscription
SDK's native tool loop and yields a flat stream of `AgentEvent`s so callers
(the brain's SSE endpoint, a converted service agent) can forward them live.

Two transports:
  * tests / fakes — pass `scenario=[...]`; `query_fn(prompt, opts, scenario)`
    yields pre-normalized events (each with `.type` / `.payload`).
  * real SDK — pass `query_fn=claude_agent_sdk.query` and `options`; the loop
    feeds a streaming-mode prompt, wires `can_use_tool` into a
    PermissionResult deny-hook, and adapts typed SDK messages to AgentEvent.

The loop is bounded by `max_turns` (counted on tool-use events) and holds a
single shared rate-limiter slot for its whole duration.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any

from matrix_shared.agent_runtime.ratelimit import AgentRateLimiter

# can_use_tool(name, params) -> bool | Awaitable[bool]
CanUseTool = Callable[[str, dict], "bool | Awaitable[bool]"]


@dataclass
class AgentEvent:
    type: str  # assistant_text | tool_use | tool_result | thinking | result | system
    payload: dict


async def _prompt_stream(prompt: str, session_id: str) -> AsyncIterator[dict]:
    """Wrap a single prompt as the streaming input the SDK needs when
    `can_use_tool` is set."""
    yield {
        "type": "user",
        "message": {"role": "user", "content": prompt},
        "parent_tool_use_id": None,
        "session_id": session_id,
    }


async def _adapt_real_sdk(iterator: Any) -> AsyncIterator[AgentEvent]:
    """Convert claude_agent_sdk typed messages into AgentEvents (by class name,
    so we don't import SDK types here)."""
    async for msg in iterator:
        cls = type(msg).__name__
        if cls == "AssistantMessage":
            for block in getattr(msg, "content", []) or []:
                bcls = type(block).__name__
                if bcls == "TextBlock":
                    yield AgentEvent("assistant_text", {"text": getattr(block, "text", "")})
                elif bcls == "ToolUseBlock":
                    yield AgentEvent(
                        "tool_use",
                        {
                            "name": getattr(block, "name", ""),
                            "params": getattr(block, "input", {}),
                            "id": getattr(block, "id", ""),
                        },
                    )
                elif bcls == "ThinkingBlock":
                    yield AgentEvent("thinking", {"text": getattr(block, "thinking", "")})
        elif cls == "UserMessage":
            for block in getattr(msg, "content", []) or []:
                if type(block).__name__ == "ToolResultBlock":
                    yield AgentEvent(
                        "tool_result",
                        {
                            "tool_use_id": getattr(block, "tool_use_id", ""),
                            "content": str(getattr(block, "content", "")),
                            "is_error": getattr(block, "is_error", False),
                        },
                    )
        elif cls == "ResultMessage":
            yield AgentEvent(
                "result",
                {
                    "total_cost_usd": getattr(msg, "total_cost_usd", None),
                    "num_turns": getattr(msg, "num_turns", None),
                    "is_error": getattr(msg, "is_error", False),
                },
            )
        else:
            yield AgentEvent("system", {"raw": cls})


async def run_agent_stream(
    *,
    prompt: str,
    query_fn: Callable,
    options: Any = None,
    scenario: list | None = None,
    session_id: str = "agent",
    max_turns: int = 12,
    can_use_tool: CanUseTool | None = None,
    limiter: AgentRateLimiter | None = None,
) -> AsyncIterator[AgentEvent]:
    """Drive a Claude tool loop, yielding normalized AgentEvents.

    Holds one shared rate-limiter slot for the whole stream. Stops after
    `max_turns` tool-use events (emitting a terminal `result` with reason).
    """
    slot = limiter.slot() if limiter is not None else nullcontext()
    async with slot:
        if scenario is not None:
            # Fake transport (tests). The fake honors options.can_use_tool.
            class _Opts:
                pass

            opts = _Opts()
            opts.can_use_tool = _sync_deny_hook(can_use_tool) if can_use_tool else None
            iterator = query_fn(prompt, opts, scenario)
            turns = 0
            async for ev in iterator:
                if ev.type == "tool_use":
                    turns += 1
                    if turns > max_turns:
                        yield AgentEvent("result", {"reason": "max_turns"})
                        return
                yield AgentEvent(ev.type, ev.payload)
            return

        # Real SDK transport.
        deferred_denial: list[str] = []
        if can_use_tool is not None and options is not None:
            from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

            async def _hook(tool_name, input_params, _ctx):
                allowed = can_use_tool(tool_name, input_params)
                if hasattr(allowed, "__await__"):
                    allowed = await allowed
                if allowed is False:
                    deferred_denial.append(tool_name)
                    return PermissionResultDeny(
                        message=f"tool denied: {tool_name}", interrupt=True
                    )
                return PermissionResultAllow(updated_input=input_params)

            options.can_use_tool = _hook

        raw = query_fn(prompt=_prompt_stream(prompt, session_id), options=options)
        turns = 0
        async for ev in _adapt_real_sdk(raw):
            if deferred_denial:
                yield AgentEvent("result", {"reason": "tool_denied", "tool": deferred_denial[0]})
                return
            if ev.type == "tool_use":
                turns += 1
                if turns > max_turns:
                    yield AgentEvent("result", {"reason": "max_turns"})
                    return
            yield ev


def _sync_deny_hook(can_use_tool: CanUseTool):
    """Adapt a can_use_tool(name, params)->bool for the fake transport, which
    calls it positionally and awaits if needed."""

    def _hook(name, params):
        return can_use_tool(name, params)

    return _hook

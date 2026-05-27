"""The SDK tool-loop driver: streaming, max_turns, deny-hook, rate limiting.

Tests use an inline fake `query_fn` (mirroring dev_agent's fake_sdk) so they
run on the host without claude_agent_sdk. A separate test drives the
real-SDK message adapter with duck-typed message objects.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from matrix_shared.agent_runtime.ratelimit import AgentRateLimiter
from matrix_shared.agent_runtime.runtime import (
    AgentEvent,
    _adapt_real_sdk,
    run_agent_stream,
)


@dataclass
class FakeEvent:
    type: str
    payload: dict


async def fake_query(prompt, options, scenario):
    """Drop-in fake for claude_agent_sdk.query(); honors options.can_use_tool."""
    can_use = getattr(options, "can_use_tool", None)
    for ev in scenario:
        if ev.type == "tool_use" and can_use is not None:
            allowed = can_use(ev.payload["name"], ev.payload.get("params", {}))
            if hasattr(allowed, "__await__"):
                allowed = await allowed
            if allowed is False:
                return
        yield ev


async def _aiter(items):
    for i in items:
        yield i


@pytest.mark.asyncio
async def test_streams_events_in_order():
    scenario = [
        FakeEvent("assistant_text", {"text": "thinking"}),
        FakeEvent("tool_use", {"name": "cypher_query", "params": {}}),
        FakeEvent("tool_result", {"content": "rows"}),
        FakeEvent("assistant_text", {"text": "answer"}),
    ]
    events = [
        e
        async for e in run_agent_stream(
            prompt="hi", query_fn=fake_query, scenario=scenario, max_turns=5
        )
    ]
    assert [e.type for e in events] == [
        "assistant_text",
        "tool_use",
        "tool_result",
        "assistant_text",
    ]


@pytest.mark.asyncio
async def test_max_turns_stops_the_loop_and_emits_reason():
    scenario = [
        FakeEvent("tool_use", {"name": "t", "params": {"i": 1}}),
        FakeEvent("tool_use", {"name": "t", "params": {"i": 2}}),
        FakeEvent("tool_use", {"name": "t", "params": {"i": 3}}),
    ]
    events = [
        e
        async for e in run_agent_stream(
            prompt="hi", query_fn=fake_query, scenario=scenario, max_turns=2
        )
    ]
    tool_uses = [e for e in events if e.type == "tool_use"]
    assert len(tool_uses) == 2
    assert events[-1].type == "result"
    assert events[-1].payload.get("reason") == "max_turns"


@pytest.mark.asyncio
async def test_can_use_tool_deny_blocks_the_call():
    scenario = [
        FakeEvent("assistant_text", {"text": "ok"}),
        FakeEvent("tool_use", {"name": "danger", "params": {}}),
        FakeEvent("tool_result", {"content": "should not appear"}),
    ]

    def deny_writes(name, params):
        return name != "danger"

    events = [
        e
        async for e in run_agent_stream(
            prompt="hi",
            query_fn=fake_query,
            scenario=scenario,
            max_turns=5,
            can_use_tool=deny_writes,
        )
    ]
    tool_names = [e.payload.get("name") for e in events if e.type == "tool_use"]
    assert "danger" not in tool_names
    assert all(
        e.payload.get("content") != "should not appear"
        for e in events
        if e.type == "tool_result"
    )


@pytest.mark.asyncio
async def test_holds_exactly_one_rate_limiter_slot_during_stream():
    limiter = AgentRateLimiter(max_concurrency=3)
    scenario = [
        FakeEvent("assistant_text", {"text": "a"}),
        FakeEvent("assistant_text", {"text": "b"}),
    ]
    seen = []
    async for _ in run_agent_stream(
        prompt="hi", query_fn=fake_query, scenario=scenario, limiter=limiter
    ):
        seen.append(limiter.in_flight)
    assert max(seen) == 1
    assert limiter.in_flight == 0


@pytest.mark.asyncio
async def test_adapt_real_sdk_maps_message_objects():
    class TextBlock:
        def __init__(self, text):
            self.text = text

    class ToolUseBlock:
        def __init__(self, name, input, id):
            self.name = name
            self.input = input
            self.id = id

    class ToolResultBlock:
        def __init__(self, tool_use_id, content, is_error):
            self.tool_use_id = tool_use_id
            self.content = content
            self.is_error = is_error

    class AssistantMessage:
        def __init__(self, content):
            self.content = content

    class UserMessage:
        def __init__(self, content):
            self.content = content

    class ResultMessage:
        def __init__(self):
            self.total_cost_usd = 0.0
            self.num_turns = 1
            self.is_error = False

    msgs = [
        AssistantMessage([TextBlock("hi"), ToolUseBlock("cypher_query", {"q": "x"}, "t1")]),
        UserMessage([ToolResultBlock("t1", "rows", False)]),
        ResultMessage(),
    ]
    out = [e async for e in _adapt_real_sdk(_aiter(msgs))]
    assert [e.type for e in out] == [
        "assistant_text",
        "tool_use",
        "tool_result",
        "result",
    ]
    assert isinstance(out[0], AgentEvent)
    assert out[1].payload["name"] == "cypher_query"
    assert out[2].payload["content"] == "rows"

"""OpenRouter backend: plan position, schema conversion, single-shot, tool loop,
429 cooldown and model fallback — all against an httpx MockTransport."""

from __future__ import annotations

import json

import httpx
import pytest

from matrix_shared import openrouter_llm as OR
from matrix_shared.agent_runtime.tool import ToolRegistry, tool
from matrix_shared.subscription_llm import _plan_backends, subscription_enabled

pytestmark = pytest.mark.asyncio


def _clear(monkeypatch):
    for k in ("MATRIX_LLM_BACKEND", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK",
              "CLAUDE_CODE_USE_VERTEX", "OPENROUTER_API_KEY", "CURSOR_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(OR, "_cooldown_until", 0.0)
    monkeypatch.setattr("matrix_shared.subscription_llm._breaker_cooldown_until", 0.0)


def test_plan_puts_openrouter_last_by_default_and_first_when_primary(monkeypatch):
    _clear(monkeypatch)
    assert _plan_backends() == [] and subscription_enabled() is False
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    assert _plan_backends() == ["openrouter"] and subscription_enabled() is True
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "tok")
    assert _plan_backends() == ["subscription", "openrouter"]
    monkeypatch.setenv("MATRIX_LLM_BACKEND", "openrouter")
    assert _plan_backends() == ["openrouter", "subscription"]


def test_openrouter_cooldown_removes_it_from_plan(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    OR._record(429, "rate limited")
    assert OR.openrouter_ready() is False
    assert _plan_backends() == []
    assert OR.openrouter_state()["cooldown_remaining_s"] > 0


def test_tool_schema_conversion_from_pytype_shorthand():
    fn = OR.tool_to_openai("recent_outcomes", "desc", {"hours": float, "verbose": bool, "ids": list})
    assert fn["type"] == "function"
    props = fn["function"]["parameters"]["properties"]
    assert props == {"hours": {"type": "number"}, "verbose": {"type": "boolean"},
                     "ids": {"type": "array", "items": {"type": "string"}}}
    schema = {"type": "object", "properties": {"x": {"type": "string"}}}
    assert OR.tool_to_openai("t", "d", schema)["function"]["parameters"] is schema


def test_resolve_model_maps_tiers_and_passes_openrouter_ids():
    assert OR.resolve_openrouter_model("haiku") == OR.OPENROUTER_IDS["haiku"]
    assert OR.resolve_openrouter_model("claude-sonnet-4-6", {"claude-sonnet-4-6": "sonnet"}) == OR.OPENROUTER_IDS["sonnet"]
    assert OR.resolve_openrouter_model("vendor/custom:free") == "vendor/custom:free"
    assert OR.resolve_openrouter_model(None) == OR.OPENROUTER_IDS["sonnet"]


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_single_shot_falls_back_on_unavailable_model(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        seen.append(body["model"])
        if body["model"] == OR.OPENROUTER_IDS["sonnet"]:
            return httpx.Response(404, json={"error": {"message": "No endpoints found for model"}})
        return httpx.Response(200, json={"model": body["model"], "usage": {"total_tokens": 42},
                                         "choices": [{"message": {"role": "assistant", "content": "  hello  "}}]})

    async with _client(handler) as c:
        text, err = await OR.openrouter_single_shot(user="hi", system="sys", model="sonnet", client=c)
    assert (text, err) == ("hello", False)
    assert seen[0] == OR.OPENROUTER_IDS["sonnet"] and seen[1] == OR.FALLBACK_MODELS[0]


async def test_single_shot_429_sets_cooldown(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "slow down"}})

    async with _client(handler) as c:
        text, err = await OR.openrouter_single_shot(user="hi", system=None, model="haiku", client=c)
    assert text is None and err is True
    assert OR.openrouter_ready() is False


async def test_agent_stream_drives_tools_and_ends_with_result(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    reg = ToolRegistry()
    calls = []

    @tool("add", "adds", {"a": int, "b": int})
    async def add(args: dict) -> dict:
        calls.append(args)
        return {"content": [{"type": "text", "text": str(args["a"] + args["b"])}]}

    reg.add(add)
    step = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        step["n"] += 1
        if step["n"] == 1:
            assert body["tools"][0]["function"]["name"] == "add"
            return httpx.Response(200, json={"model": "m", "choices": [{"message": {
                "role": "assistant", "content": None,
                "tool_calls": [{"id": "c1", "type": "function",
                                "function": {"name": "add", "arguments": json.dumps({"a": 2, "b": 3})}}]}}]})
        # second turn: tool result must be in the transcript
        assert body["messages"][-1] == {"role": "tool", "tool_call_id": "c1", "content": "5"}
        return httpx.Response(200, json={"model": "m", "choices": [{"message": {
            "role": "assistant", "content": "The sum is 5."}}]})

    events = []
    async with _client(handler) as c:
        async for ev in OR.openrouter_agent_stream(prompt="add 2 and 3", system="s", model="sonnet",
                                                   tool_registry=reg, max_turns=5, client=c):
            events.append(ev)
    types = [e.type for e in events]
    assert types == ["tool_use", "tool_result", "assistant_text", "result"]
    assert calls == [{"a": 2, "b": 3}]
    assert events[-1].payload["is_error"] is False and events[-1].payload["num_turns"] == 1


async def test_agent_stream_denies_tools_outside_can_use(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    reg = ToolRegistry()

    @tool("danger", "x", {})
    async def danger(args: dict) -> dict:
        raise AssertionError("must not run")

    reg.add(danger)
    step = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        step["n"] += 1
        if step["n"] == 1:
            return httpx.Response(200, json={"model": "m", "choices": [{"message": {
                "role": "assistant", "content": None,
                "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "danger", "arguments": "{}"}}]}}]})
        return httpx.Response(200, json={"model": "m", "choices": [{"message": {"role": "assistant", "content": "ok"}}]})

    async with _client(handler) as c:
        events = [ev async for ev in OR.openrouter_agent_stream(
            prompt="p", tool_registry=reg, can_use_tool=lambda n, a: False, client=c)]
    tr = next(e for e in events if e.type == "tool_result")
    assert tr.payload["is_error"] is True and "not permitted" in tr.payload["content"]


def test_subscription_ready_via_credentials_file(monkeypatch, tmp_path):
    from matrix_shared import subscription_llm as S
    _clear(monkeypatch)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    assert S._subscription_ready() is False and S.subscription_enabled() is False
    (tmp_path / ".credentials.json").write_text('{"claudeAiOauth": {"accessToken": "x"}}')
    assert S._subscription_ready() is True and S.subscription_enabled() is True
    assert _plan_backends() == ["subscription"]

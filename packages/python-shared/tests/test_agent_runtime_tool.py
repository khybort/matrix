"""Tool / ToolRegistry / @tool with side-effect classification.

The registry is the contract a tool belt is built from. The brain asserts
its belt is entirely read-only before exposing it to the model; agents that
legitimately write classify those tools explicitly.
"""

from __future__ import annotations

import pytest

from matrix_shared.agent_runtime.tool import (
    NonReadOnlyToolError,
    Tool,
    ToolRegistry,
    assert_all_read_only,
    mcp_tool_names,
    tool,
)


def _schema():
    return {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}


def test_tool_decorator_builds_a_tool():
    @tool("echo", "echoes q", _schema())
    async def echo(args):
        return {"content": [{"type": "text", "text": args["q"]}]}

    assert isinstance(echo, Tool)
    assert echo.name == "echo"
    assert echo.side_effect == "read"
    assert echo.description == "echoes q"


@pytest.mark.asyncio
async def test_tool_handler_is_callable():
    @tool("echo", "echoes q", _schema())
    async def echo(args):
        return {"content": [{"type": "text", "text": args["q"]}]}

    out = await echo.handler({"q": "hi"})
    assert out["content"][0]["text"] == "hi"


def test_registry_add_get_contains():
    reg = ToolRegistry()

    @tool("a", "d", _schema())
    async def a(args):
        return {}

    reg.add(a)
    assert "a" in reg
    assert reg.get("a") is a
    assert reg.names() == ["a"]
    assert len(reg) == 1


def test_registry_rejects_duplicate_name():
    reg = ToolRegistry()

    @tool("a", "d", _schema())
    async def a(args):
        return {}

    reg.add(a)
    with pytest.raises(ValueError):
        reg.add(a)


def test_assert_all_read_only_passes_for_read_belt():
    reg = ToolRegistry()

    @tool("a", "d", _schema())
    async def a(args):
        return {}

    reg.add(a)
    assert_all_read_only(reg)  # no raise


def test_assert_all_read_only_rejects_write_tool_and_names_it():
    reg = ToolRegistry()

    @tool("danger", "writes", _schema(), side_effect="write")
    async def danger(args):
        return {}

    reg.add(danger)
    with pytest.raises(NonReadOnlyToolError) as exc:
        assert_all_read_only(reg)
    assert "danger" in str(exc.value)


def test_mcp_tool_names_are_namespaced():
    reg = ToolRegistry()

    @tool("cypher_query", "d", _schema())
    async def cq(args):
        return {}

    reg.add(cq)
    assert mcp_tool_names("matrix", reg) == ["mcp__matrix__cypher_query"]

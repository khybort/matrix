"""Tool abstraction for agent tool belts.

A `Tool` is a typed, schema-described async callable with an explicit
side-effect class. Tools are the only way an agent touches the world. The
registry is converted to a Claude Code in-process MCP server
(`build_sdk_mcp_server`) so the subscription SDK drives the tool loop —
the same proven path as `services/dev_agent/src/dev_agent/sdk_runner.py`.

Side-effect classes:
    read        — never mutates state (brain belt must be entirely this)
    write       — mutates non-money state (advisory writes: lessons, proposals)
    risk-gated  — could move money; the handler MUST call the deterministic
                  trading-safety gate internally and cannot be satisfied by
                  LLM output. The LLM is never on a gate's allow-path.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

SideEffect = Literal["read", "write", "risk-gated"]

ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler
    side_effect: SideEffect = "read"


def tool(
    name: str,
    description: str,
    input_schema: dict[str, Any],
    *,
    side_effect: SideEffect = "read",
) -> Callable[[ToolHandler], Tool]:
    """Decorator: wrap an async handler into a `Tool`."""

    def deco(fn: ToolHandler) -> Tool:
        return Tool(
            name=name,
            description=description,
            input_schema=input_schema,
            handler=fn,
            side_effect=side_effect,
        )

    return deco


class ToolRegistry:
    """An ordered, name-unique collection of tools."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def add(self, t: Tool) -> Tool:
        if t.name in self._tools:
            raise ValueError(f"duplicate tool name: {t.name}")
        self._tools[t.name] = t
        return t

    def get(self, name: str) -> Tool:
        return self._tools[name]

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def __iter__(self):
        return iter(self._tools.values())


class NonReadOnlyToolError(RuntimeError):
    """Raised when a belt expected to be read-only contains a writing tool."""


def assert_all_read_only(registry: ToolRegistry) -> None:
    """Guarantee every tool in `registry` is side_effect='read'."""
    offenders = [t.name for t in registry.all() if t.side_effect != "read"]
    if offenders:
        raise NonReadOnlyToolError(
            f"non-read-only tools in a read-only belt: {', '.join(offenders)}"
        )


def mcp_tool_names(server_name: str, registry: ToolRegistry) -> list[str]:
    """The model-visible `mcp__<server>__<tool>` names for `allowed_tools`."""
    return [f"mcp__{server_name}__{t.name}" for t in registry.all()]


def build_sdk_mcp_server(server_name: str, registry: ToolRegistry, version: str = "1.0.0"):
    """Build a Claude Code in-process MCP server from the registry.

    Lazy SDK import so the registry/guards stay importable (and testable) in
    environments without `claude_agent_sdk` installed (e.g. host unit tests).
    """
    from claude_agent_sdk import create_sdk_mcp_server
    from claude_agent_sdk import tool as sdk_tool

    sdk_tools = [
        sdk_tool(t.name, t.description, t.input_schema)(t.handler)
        for t in registry.all()
    ]
    return create_sdk_mcp_server(server_name, version, sdk_tools)

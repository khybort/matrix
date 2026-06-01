"""Brain tool-loop runtime — wires the read-only belt to the subscription SDK."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from matrix_shared.agent_runtime.ratelimit import get_rate_limiter
from matrix_shared.agent_runtime.tool import build_sdk_mcp_server, mcp_tool_names
from matrix_shared.subscription_llm import call_subscription_agent

from brain.access import make_deny_hook
from brain.db import Pools
from brain.prompt import BRAIN_SYSTEM
from brain.tools import build_registry

# SDK built-in tools that could write/execute — explicitly forbidden.
WRITE_BUILTINS = ["Bash", "Write", "Edit", "NotebookEdit"]

_SERVER_NAME = "matrix"


class BrainRuntime:
    """Holds the in-process MCP server + allow-list, runs one chat turn."""

    def __init__(self, pools: Pools, *, model: str, max_turns: int) -> None:
        self.registry = build_registry(pools)  # asserts read-only inside
        self.server = build_sdk_mcp_server(_SERVER_NAME, self.registry)
        self.allowed = mcp_tool_names(_SERVER_NAME, self.registry)
        self.deny = make_deny_hook(self.allowed)
        self.model = model
        self.max_turns = max_turns

    async def run(
        self, prompt: str, *, session_id: str, model: str | None = None
    ) -> AsyncIterator[Any]:
        """Yield AgentEvents for one prompt. `model` overrides the configured
        default for this turn only (e.g. operator wants Opus for one hard
        question)."""
        async for ev in call_subscription_agent(
            prompt=prompt,
            system=BRAIN_SYSTEM,
            model=model or self.model,
            mcp_servers={_SERVER_NAME: self.server},
            allowed_tools=self.allowed,
            disallowed_tools=WRITE_BUILTINS,
            can_use_tool=self.deny,
            max_turns=self.max_turns,
            session_id=session_id,
            limiter=get_rate_limiter(),
            tool_registry=self.registry,
            mcp_server_name=_SERVER_NAME,
        ):
            yield ev

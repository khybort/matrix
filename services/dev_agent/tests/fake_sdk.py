"""Scripted async generator that mimics claude_agent_sdk.query().

Each scenario YAML is a flat list of events. The fake_sdk yields them in
order, allowing the safety hook (can_use_tool) to abort mid-stream.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator

import yaml


@dataclass
class FakeEvent:
    type: str
    payload: dict


def load_scenario(name: str) -> list[FakeEvent]:
    path = Path(__file__).parent / "scenarios" / f"{name}.yaml"
    raw = yaml.safe_load(path.read_text())
    return [FakeEvent(type=e.pop("type"), payload=e) for e in raw["events"]]


async def fake_query(prompt: str, options, scenario: list[FakeEvent]) -> AsyncIterator[FakeEvent]:
    """Drop-in replacement for claude_agent_sdk.query() for tests.

    Honors options.can_use_tool: if it returns False (or raises), the iteration
    stops at that event (the tool is not executed, and no tool_result is yielded).
    """
    can_use = getattr(options, "can_use_tool", None)
    for ev in scenario:
        if ev.type == "tool_use" and can_use is not None:
            allowed = can_use(ev.payload["name"], ev.payload.get("params", {}))
            if hasattr(allowed, "__await__"):
                allowed = await allowed
            if allowed is False:
                return
        yield ev

"""can_use_tool deny-hook — defense in depth for the read-only Brain.

Even though only read tools are registered and `disallowed_tools` bans the
SDK's built-in writers, this hook hard-denies anything not on the explicit
allow-list, so a tool the model invents or a built-in we forgot can never run.
"""

from __future__ import annotations

from collections.abc import Iterable


def make_deny_hook(allowed_tool_names: Iterable[str]):
    """Return a can_use_tool(name, params)->bool that allows only `allowed`."""
    allowed = frozenset(allowed_tool_names)

    def hook(name: str, params: dict) -> bool:
        return name in allowed

    return hook

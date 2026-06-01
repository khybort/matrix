"""Build the 5-or-6 layer system prompt for the dev_agent."""

from __future__ import annotations

from typing import Any


BASE_LAYER = """You are the Matrix dev_agent. You are working in an isolated git worktree.

Rules:
- Follow CLAUDE.md conventions strictly (small atomic commits, no defensive coding, no half-finished implementations).
- Path policy: FORBIDDEN_PATHS is empty — you may Edit/Write anywhere in the worktree, including services/strategy/, services/agent/, and services/execution/. To restore default-deny, repopulate FORBIDDEN_PATHS in dev_agent/config.py.
- Live trading safety is enforced at runtime in services/execution (certificate gates, kill switch); do not weaken or bypass those mechanisms in code you change.
- Trading risk gates are non-negotiable. Bypass attempts will fail the task.
- Stay within the worktree directory. Do not push to remote. Do not modify files outside this worktree.
"""


def build_system_prompt(
    *,
    task_description: str,
    lessons: list[dict[str, Any]],
    codebase_ctx: str,
    auto_commit: bool,
    auto_pr: bool,
    run_tests: bool,
    max_turns: int,
    handoff_snapshot: dict[str, Any] | None,
    touches_files: list[str],
) -> str:
    parts: list[str] = [BASE_LAYER]

    if lessons:
        parts.append("\n## LESSONS (apply these before acting)\n")
        for i, l in enumerate(lessons, start=1):
            parts.append(f"- ({i}) topic={l['topic']!r}")
            if l.get("anti_pattern"):
                parts.append(f"      anti_pattern: {l['anti_pattern']}")
            parts.append(f"      correct_approach: {l['correct_approach']}")

    if codebase_ctx:
        parts.append("\n## CODEBASE CONTEXT\n")
        parts.append(codebase_ctx)

    parts.append("\n## TASK\n")
    parts.append(task_description)
    if touches_files:
        parts.append(f"\nHint — touches_files: {touches_files}")

    parts.append("\n## CONSTRAINTS\n")
    parts.append(
        f"auto_commit={auto_commit}, auto_pr={auto_pr}, "
        f"run_tests={run_tests}, max_turns={max_turns}"
    )

    if handoff_snapshot:
        parts.append("\n## HANDOFF_CONTEXT\n")
        parts.append(_render_snapshot(handoff_snapshot))

    return "\n".join(parts)


def _render_snapshot(s: dict[str, Any]) -> str:
    lines = []
    if "summary" in s:
        lines.append(s["summary"])
    if "open_files" in s:
        lines.append("Open files: " + ", ".join(s["open_files"]))
    if "messages" in s:
        lines.append("Recent messages:")
        for m in s["messages"][-20:]:
            lines.append(f"  - {m}")
    return "\n".join(lines)

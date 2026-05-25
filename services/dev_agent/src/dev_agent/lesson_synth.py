"""Generate draft lessons from failed tasks.

Phase 0 uses a Haiku call by default (cheap: ~$0.001/task). The LLM
is injected for testability — tests pass a mock.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

import asyncpg

from dev_agent.memory import write_lesson_draft

NOTABLE_FAILURES: frozenset[str] = frozenset({
    "max_turns", "tool_loop", "trading_path_violation", "test_broke",
})


async def maybe_synthesize_lesson_for_failure(
    pool: asyncpg.Pool,
    *,
    task_id: int,
    llm: Callable[..., Awaitable[dict[str, Any]]],
) -> int | None:
    task = await pool.fetchrow("SELECT * FROM dev_tasks WHERE id=$1", task_id)
    if not task or task["failure_reason"] not in NOTABLE_FAILURES:
        return None

    last_events = await pool.fetch(
        "SELECT event_type, payload FROM dev_task_events WHERE task_id=$1 "
        "ORDER BY seq DESC LIMIT 10",
        task_id,
    )
    snippet = "\n".join(f"{e['event_type']}: {e['payload']}" for e in reversed(last_events))

    payload = await llm(
        task_description=task["description"],
        failure_reason=task["failure_reason"],
        events_snippet=snippet,
        review_notes=task["review_notes"],
        touches_files=list(task["touches_files"] or []),
    )

    return await write_lesson_draft(
        pool,
        source="failure",
        topic=payload["topic"],
        summary=payload["summary"],
        anti_pattern=payload.get("anti_pattern"),
        correct_approach=payload["correct_approach"],
        relevant_paths=payload.get("relevant_paths", []),
        origin_task_id=task_id,
    )


async def real_haiku_llm(**kwargs: Any) -> dict[str, Any]:
    """Phase 0 real LLM call via the `claude` CLI (Claude Code subscription).

    We deliberately do NOT use the Anthropic API SDK here: dev_agent runs
    entirely on the user's Claude Code subscription, not API-key billing.
    The `claude` CLI is already installed in the dev_agent image and
    authenticates via CLAUDE_CODE_OAUTH_TOKEN (set in compose).
    """
    import asyncio
    import json

    user_prompt = _build_prompt(**kwargs)
    proc = await asyncio.create_subprocess_exec(
        "claude",
        "-p", user_prompt,
        "--output-format", "json",
        "--model", "claude-haiku-4-5",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI failed: {stderr.decode(errors='replace')}")

    envelope = json.loads(stdout.decode())
    if envelope.get("is_error"):
        raise RuntimeError(f"claude CLI returned error: {envelope}")
    text = envelope["result"]
    return json.loads(text[text.find("{"): text.rfind("}") + 1])


def _build_prompt(*, task_description, failure_reason, events_snippet, review_notes, touches_files) -> str:
    return f"""You are summarizing a failed Matrix dev_agent task into a lesson.
Output strict JSON with keys: topic, summary, anti_pattern, correct_approach, relevant_paths.

Failure reason: {failure_reason}
Task: {task_description}
Touches files: {touches_files}
Review notes: {review_notes or '(none)'}
Last events:
{events_snippet}

Rules:
- topic: short kebab-case slug
- summary: one sentence
- anti_pattern: what the agent did wrong (may be null)
- correct_approach: one or two sentences
- relevant_paths: array of file/dir prefixes where this lesson applies
"""

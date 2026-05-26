"""Project-wide LLM helper backed by the Claude Code subscription.

Routes every LLM call through `claude_agent_sdk.query` so the spawned
`claude` CLI uses `CLAUDE_CODE_OAUTH_TOKEN` (subscription auth) instead
of `ANTHROPIC_API_KEY` (per-token billing). Same contract as the old
`call_claude` / `call_claude_json` helpers — None on any failure.

Default model: Sonnet 4.6. Subscription is flat-rate so per-call $ = 0
from the operator's standpoint; the new bottleneck is rate limits.
"""

from __future__ import annotations

import os

import orjson
from loguru import logger

DEFAULT_MODEL = "claude-sonnet-4-6"


def subscription_enabled() -> bool:
    """True when the Claude Code OAuth token is configured."""
    return bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"))


async def call_subscription(
    *,
    user: str,
    system: str | None = None,
    model: str | None = None,
    max_tokens: int = 800,
    temperature: float = 0.2,
) -> str | None:
    """Send one prompt through the subscription path; return text or None.

    No tools, no MCP, no permission prompts — single-shot text generation.
    max_tokens and temperature are accepted for API parity with the old
    httpx-based helper but the SDK doesn't expose them today; they're
    informational. If a future SDK version exposes them, plumb through.
    """
    if not subscription_enabled():
        return None

    try:
        from claude_agent_sdk import ClaudeAgentOptions, query
    except ImportError as e:
        logger.warning(f"claude_agent_sdk import failed: {e}")
        return None

    # `acceptEdits` instead of `bypassPermissions`: the CLI refuses to spawn
    # with --dangerously-skip-permissions when running as root (containers do),
    # but acceptEdits is fine and there are no tools to permit anyway.
    options = ClaudeAgentOptions(
        system_prompt=system or "",
        model=model or DEFAULT_MODEL,
        permission_mode="acceptEdits",
        allowed_tools=[],
    )

    chunks: list[str] = []
    try:
        async for ev in query(prompt=user, options=options):
            # Assistant messages carry content blocks; concatenate text.
            content = getattr(ev, "content", None)
            if not content:
                continue
            if isinstance(content, list):
                for block in content:
                    text = getattr(block, "text", None)
                    if isinstance(text, str):
                        chunks.append(text)
                    elif isinstance(block, dict) and block.get("type") == "text":
                        chunks.append(str(block.get("text", "")))
            elif isinstance(content, str):
                chunks.append(content)
    except Exception as e:
        logger.warning(f"subscription_llm: {e}")
        return None

    text = "".join(chunks).strip()
    return text or None


async def call_subscription_json(
    *,
    user: str,
    system: str | None = None,
    model: str | None = None,
    max_tokens: int = 1200,
    temperature: float = 0.2,
) -> dict | None:
    """call_subscription + parse JSON, tolerating ```json fences."""
    text = await call_subscription(
        user=user,
        system=system,
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    if not text:
        return None
    if text.startswith("```"):
        text = "\n".join(
            ln for ln in text.splitlines() if not ln.strip().startswith("```")
        ).strip()
    try:
        parsed = orjson.loads(text)
    except orjson.JSONDecodeError:
        logger.warning(f"subscription_llm: response not valid JSON: {text[:200]}")
        return None
    return parsed if isinstance(parsed, dict) else None

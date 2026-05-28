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
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import orjson
from loguru import logger

# Model indirection — env-resolvable so a Bedrock/Vertex operator can swap
# the IDs without code changes. Defaults match the Anthropic API short names
# used by the subscription path (claude_agent_sdk → claude CLI on
# CLAUDE_CODE_OAUTH_TOKEN). For Bedrock, set e.g.
#   MATRIX_MODEL_SONNET=us.anthropic.claude-sonnet-4-6-20250929-v1:0
# in .env; for Vertex, the equivalent vertex-style ID.
MODEL_HAIKU = os.environ.get("MATRIX_MODEL_HAIKU", "claude-haiku-4-5-20251001")
MODEL_SONNET = os.environ.get("MATRIX_MODEL_SONNET", "claude-sonnet-4-6")
MODEL_OPUS = os.environ.get("MATRIX_MODEL_OPUS", "claude-opus-4-7")

DEFAULT_MODEL = MODEL_SONNET


def subscription_enabled() -> bool:
    """True when any supported LLM backend is configured.

    Backends, in priority order:
      1. Claude Code subscription — `CLAUDE_CODE_OAUTH_TOKEN` set. Default.
      2. AWS Bedrock — `CLAUDE_CODE_USE_BEDROCK=1` + AWS creds in env.
      3. Google Vertex — `CLAUDE_CODE_USE_VERTEX=1` + Google creds in env.

    The `claude_agent_sdk` (via the bundled `claude` CLI) handles backend
    selection itself based on these env vars; we just need *one* of them to
    be true for the LLM path to fire. The historical name `subscription_*`
    is kept for backward compatibility — read it as "is LLM path ready?".
    """
    return bool(
        os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
        or os.environ.get("CLAUDE_CODE_USE_BEDROCK")
        or os.environ.get("CLAUDE_CODE_USE_VERTEX")
    )


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
    result_cost: float | None = None
    result_is_err: bool | None = None
    try:
        async for ev in query(prompt=user, options=options):
            # ResultMessage carries the per-call cost / error flag so we can
            # surface single-shot usage (Haiku workers, fallback LLM paths)
            # via the same `agent.usage` channel as the agent loop.
            if type(ev).__name__ == "ResultMessage":
                rc = getattr(ev, "total_cost_usd", None)
                if isinstance(rc, (int, float)):
                    result_cost = float(rc)
                result_is_err = bool(getattr(ev, "is_error", False))
                continue
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
    # One agent.usage line per single-shot call too — turns=1 by definition.
    logger.info(
        "agent.usage session=single_shot model={m} turns=1 cost_usd={c} is_error={e}",
        m=model or DEFAULT_MODEL,
        c=f"{result_cost:.6f}" if result_cost is not None else None,
        e=result_is_err,
    )
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


async def call_subscription_agent(
    *,
    prompt: str,
    system: str | None = None,
    model: str | None = None,
    mcp_servers: dict[str, Any] | None = None,
    allowed_tools: list[str] | None = None,
    disallowed_tools: list[str] | None = None,
    can_use_tool: Callable[[str, dict], bool | Awaitable[bool]] | None = None,
    max_turns: int = 12,
    session_id: str = "agent",
    limiter: Any | None = None,
) -> AsyncIterator[Any]:
    """Run a multi-step SDK tool loop on the subscription path; yield AgentEvents.

    Unlike call_subscription (single-shot, no tools), this exposes the SDK's
    native tool loop via in-process MCP servers — still authenticated by
    CLAUDE_CODE_OAUTH_TOKEN, no raw Anthropic API. Yields nothing when the
    subscription is not configured (caller falls back to a deterministic path).
    """
    if not subscription_enabled():
        return

    try:
        from claude_agent_sdk import ClaudeAgentOptions, query
    except ImportError as e:
        logger.warning(f"claude_agent_sdk import failed: {e}")
        return

    from matrix_shared.agent_runtime.runtime import run_agent_stream

    options = ClaudeAgentOptions(
        system_prompt=system or "",
        model=model or DEFAULT_MODEL,
        permission_mode="acceptEdits",
        mcp_servers=mcp_servers or {},
        allowed_tools=allowed_tools or [],
        disallowed_tools=disallowed_tools or [],
        setting_sources=[],
    )

    resolved_model = model or DEFAULT_MODEL
    async for ev in run_agent_stream(
        prompt=prompt,
        query_fn=query,
        options=options,
        can_use_tool=can_use_tool,
        max_turns=max_turns,
        session_id=session_id,
        limiter=limiter,
    ):
        # One structured `agent.usage` line per completion — feeds `make
        # agent-usage`. Subscription is flat-$ so cost is informational; the
        # number that matters for the trading loop is `turns` (rate budget).
        if ev.type == "result":
            payload = ev.payload or {}
            cost = payload.get("total_cost_usd")
            turns = payload.get("num_turns")
            is_err = payload.get("is_error")
            reason = payload.get("reason")
            extras = f" reason={reason}" if reason else ""
            logger.info(
                "agent.usage session={s} model={m} turns={t} "
                "cost_usd={c} is_error={e}{x}",
                s=session_id, m=resolved_model, t=turns,
                c=f"{cost:.6f}" if isinstance(cost, (int, float)) else cost,
                e=is_err, x=extras,
            )
            payload.setdefault("model", resolved_model)
        yield ev

"""Project-wide LLM helper backed by the Claude Code subscription.

Routes every LLM call through `claude_agent_sdk.query` so the spawned
`claude` CLI uses `CLAUDE_CODE_OAUTH_TOKEN` (subscription auth) instead
of `ANTHROPIC_API_KEY` (per-token billing). Same contract as the old
`call_claude` / `call_claude_json` helpers — None on any failure.

Default model: Sonnet 4.6. Subscription is flat-rate so per-call $ = 0
from the operator's standpoint; the new bottleneck is rate limits.
"""

from __future__ import annotations

import asyncio
import os
import time
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

def _resolve_default_model() -> str:
    """Project-wide default tier for callers that don't pin a model.

    Env-resolvable so `make llm-haiku` / `make llm-sonnet` flip the decision +
    low-stakes LLM path (agent decision override, lessons feeder dev_tasks,
    bulletin) between tiers without code edits. Quality-critical callers
    (graph extract, synthesis, reflection) pin MODEL_SONNET explicitly and are
    unaffected by this knob. Accepts a tier name (haiku|sonnet|opus) or a raw
    model id.
    """
    raw = os.environ.get("MATRIX_DEFAULT_MODEL", "sonnet").strip()
    return {"haiku": MODEL_HAIKU, "sonnet": MODEL_SONNET, "opus": MODEL_OPUS}.get(
        raw.lower(), raw or MODEL_SONNET
    )


DEFAULT_MODEL = _resolve_default_model()


# --------------------------------------------------------------------------
# Backend selection + Bedrock→subscription failover
#
# Two backends share one contract. The active backend is chosen PER CALL:
# claude_agent_sdk merges ClaudeAgentOptions.env over the process env, so we
# flip CLAUDE_CODE_USE_BEDROCK + AWS creds per spawn without touching .env or
# restarting. State below is per-process (each service runs its own container,
# so each fails over on its own experience — no shared state needed).
#
# Policy (set by `make llm-subscription` / `make llm-bedrock` writing .env):
#   * subscription mode  → subscription only, no failover.
#   * bedrock mode       → Bedrock is PRIMARY. On sustained failure
#     (>= MATRIX_BEDROCK_FAILOVER_GRACE_S, default 600s, with exponential probe
#     backoff between attempts) we fail over to the subscription so the LLM
#     path keeps working. The deterministic algorithms never block: a degraded
#     call returns None and the caller uses its rule-based path. When a probe
#     succeeds again, Bedrock is restored as primary.
# Bedrock mode needs BOTH creds present (AWS + OAuth); `make llm-bedrock` keeps
# CLAUDE_CODE_OAUTH_TOKEN in .env for exactly this fallback.
# --------------------------------------------------------------------------

# Canonical per-backend model ids by tier. Subscription = Anthropic short
# names; Bedrock = cross-region inference-profile ids. Both overridable via env.
_SUBSCRIPTION_IDS = {
    "haiku": os.environ.get("MATRIX_SUB_MODEL_HAIKU", "claude-haiku-4-5-20251001"),
    "sonnet": os.environ.get("MATRIX_SUB_MODEL_SONNET", "claude-sonnet-4-6"),
    "opus": os.environ.get("MATRIX_SUB_MODEL_OPUS", "claude-opus-4-7"),
}
_BEDROCK_IDS = {
    "haiku": os.environ.get(
        "MATRIX_BEDROCK_MODEL_HAIKU", "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    ),
    "sonnet": os.environ.get("MATRIX_BEDROCK_MODEL_SONNET", "us.anthropic.claude-sonnet-4-6"),
    "opus": os.environ.get("MATRIX_BEDROCK_MODEL_OPUS", "us.anthropic.claude-opus-4-7"),
}
# Reverse map: any known id, tier token, or current MATRIX_MODEL_* value → tier.
_TIER_OF: dict[str, str] = {}
for _t in ("haiku", "sonnet", "opus"):
    _TIER_OF[_t] = _t
    _TIER_OF[_SUBSCRIPTION_IDS[_t]] = _t
    _TIER_OF[_BEDROCK_IDS[_t]] = _t
for _t, _mid in (("haiku", MODEL_HAIKU), ("sonnet", MODEL_SONNET), ("opus", MODEL_OPUS)):
    _TIER_OF.setdefault(_mid, _t)


def _resolve_model(model: str | None, backend: str) -> str:
    """Map a caller-supplied model (id or tier token) to the backend's own id."""
    requested = model or DEFAULT_MODEL
    tier = _TIER_OF.get(requested)
    if tier is None:
        return requested  # unknown id — pass through untouched
    return (_BEDROCK_IDS if backend == "bedrock" else _SUBSCRIPTION_IDS)[tier]


def _backend_options_env(backend: str) -> dict[str, str]:
    """Per-spawn env that forces a specific backend (merged over process env)."""
    if backend == "bedrock":
        env = {"CLAUDE_CODE_USE_BEDROCK": "1"}
        for k in (
            "AWS_REGION", "AWS_DEFAULT_REGION", "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_BEARER_TOKEN_BEDROCK",
        ):
            v = os.environ.get(k)
            if v:
                env[k] = v
        return env
    # subscription: fully neutralise Bedrock for this spawn so the CLI falls back
    # to CLAUDE_CODE_OAUTH_TOKEN (inherited). Blanking USE_BEDROCK alone is not
    # enough when AWS creds are present in the process env — the CLI still routes
    # to Bedrock — so we blank the AWS creds for this spawn too.
    return {
        "CLAUDE_CODE_USE_BEDROCK": "",
        "AWS_ACCESS_KEY_ID": "",
        "AWS_SECRET_ACCESS_KEY": "",
        "AWS_SESSION_TOKEN": "",
        "AWS_BEARER_TOKEN_BEDROCK": "",
    }


# Per-attempt wall-clock cap. A down/misconfigured backend (e.g. bad Bedrock
# creds) can otherwise make the spawned CLI hang on SDK retries for minutes,
# stalling the 15s decision loop. Bounded so failover is fast.
_CALL_TIMEOUT_S = float(os.environ.get("MATRIX_LLM_CALL_TIMEOUT_S", "45"))

_FAILOVER_GRACE_S = float(os.environ.get("MATRIX_BEDROCK_FAILOVER_GRACE_S", "600"))
_PROBE_BASE_S = float(os.environ.get("MATRIX_BEDROCK_PROBE_BASE_S", "30"))
_PROBE_MAX_S = float(os.environ.get("MATRIX_BEDROCK_PROBE_MAX_S", "600"))

_bedrock_down_since: float | None = None
_bedrock_demoted = False
_bedrock_next_try = 0.0
_bedrock_backoff = 0.0


def _bedrock_primary() -> bool:
    return bool(os.environ.get("CLAUDE_CODE_USE_BEDROCK"))


def _subscription_ready() -> bool:
    return bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"))


def backend_state() -> dict[str, Any]:
    """Observability snapshot of the failover state machine (for dashboards)."""
    now = time.monotonic()
    return {
        "primary": "bedrock" if _bedrock_primary() else "subscription",
        "bedrock_demoted": _bedrock_demoted,
        "bedrock_down_for_s": (now - _bedrock_down_since) if _bedrock_down_since else 0.0,
        "bedrock_next_try_in_s": max(0.0, _bedrock_next_try - now),
        "subscription_available": _subscription_ready(),
    }


def _plan_backends() -> list[str]:
    """Ordered backends to attempt for this call (empty list = degraded → None)."""
    now = time.monotonic()
    if not _bedrock_primary():
        return ["subscription"] if _subscription_ready() else []
    sub = ["subscription"] if _subscription_ready() else []
    if _bedrock_demoted:
        # On subscription now; periodically probe Bedrock to restore it.
        return (["bedrock"] + sub) if now >= _bedrock_next_try else sub
    # Within the grace window: keep trying Bedrock (respect probe backoff); do
    # NOT use the subscription yet — sustained failure must persist GRACE_S first.
    if now >= _bedrock_next_try:
        return ["bedrock"]
    return []


def _record_bedrock(ok: bool) -> None:
    global _bedrock_down_since, _bedrock_demoted, _bedrock_next_try, _bedrock_backoff
    now = time.monotonic()
    if ok:
        if _bedrock_demoted or _bedrock_down_since is not None:
            logger.info("subscription_llm: Bedrock healthy again → restored as primary")
        _bedrock_down_since = None
        _bedrock_demoted = False
        _bedrock_backoff = 0.0
        _bedrock_next_try = 0.0
        return
    if _bedrock_down_since is None:
        _bedrock_down_since = now
    _bedrock_backoff = _PROBE_BASE_S if _bedrock_backoff <= 0 else min(_PROBE_MAX_S, _bedrock_backoff * 2)
    _bedrock_next_try = now + _bedrock_backoff
    if not _bedrock_demoted and (now - _bedrock_down_since) >= _FAILOVER_GRACE_S:
        _bedrock_demoted = True
        logger.warning(
            "subscription_llm: Bedrock unhealthy for "
            f"{now - _bedrock_down_since:.0f}s (>= {_FAILOVER_GRACE_S:.0f}s) "
            "→ failing over to Claude subscription"
        )


# Quota-aware circuit breaker — when the Claude Code subscription hits its
# rolling cap, every call comes back instantly as ResultMessage(is_error=True,
# cost=0). Without a breaker the system burns rate budget in a tight loop
# (observed: 33/52 graph_extract calls failed during a 4h quota window).
# After N consecutive errors we open the breaker for COOLDOWN_S seconds; while
# open, callers get None immediately and fall back to deterministic paths.
_BREAKER_THRESHOLD = int(os.environ.get("MATRIX_LLM_BREAKER_THRESHOLD", "5"))
_BREAKER_COOLDOWN_S = float(os.environ.get("MATRIX_LLM_BREAKER_COOLDOWN_S", "900"))
_breaker_consecutive_errors = 0
_breaker_cooldown_until = 0.0


def breaker_is_open() -> bool:
    return time.monotonic() < _breaker_cooldown_until


def breaker_state() -> dict[str, float | int | bool]:
    now = time.monotonic()
    remaining = max(0.0, _breaker_cooldown_until - now)
    return {
        "open": now < _breaker_cooldown_until,
        "consecutive_errors": _breaker_consecutive_errors,
        "cooldown_remaining_s": remaining,
    }


def _breaker_record(is_error: bool | None) -> None:
    global _breaker_consecutive_errors, _breaker_cooldown_until
    if is_error:
        _breaker_consecutive_errors += 1
        if _breaker_consecutive_errors >= _BREAKER_THRESHOLD and not breaker_is_open():
            _breaker_cooldown_until = time.monotonic() + _BREAKER_COOLDOWN_S
            logger.warning(
                f"subscription_llm: circuit breaker OPEN after "
                f"{_breaker_consecutive_errors} consecutive errors; "
                f"cooling down {_BREAKER_COOLDOWN_S:.0f}s"
            )
    else:
        if _breaker_consecutive_errors > 0 or _breaker_cooldown_until > 0:
            logger.info(
                f"subscription_llm: circuit breaker reset after success "
                f"(was {_breaker_consecutive_errors} consecutive errors)"
            )
        _breaker_consecutive_errors = 0
        _breaker_cooldown_until = 0.0


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


async def _single_shot_once(
    backend: str, *, user: str, system: str | None, model: str | None
) -> tuple[str | None, bool]:
    """One single-shot SDK call on a specific backend. Returns (text, is_error)."""
    try:
        from claude_agent_sdk import ClaudeAgentOptions, query
    except ImportError as e:
        logger.warning(f"claude_agent_sdk import failed: {e}")
        return None, True

    resolved = _resolve_model(model, backend)
    # `acceptEdits` instead of `bypassPermissions`: the CLI refuses to spawn
    # with --dangerously-skip-permissions when running as root (containers do),
    # but acceptEdits is fine and there are no tools to permit anyway.
    options = ClaudeAgentOptions(
        system_prompt=system or "",
        model=resolved,
        permission_mode="acceptEdits",
        allowed_tools=[],
        env=_backend_options_env(backend),
    )

    chunks: list[str] = []
    result_cost: float | None = None
    result_is_err: bool | None = None

    async def _drive() -> None:
        nonlocal result_cost, result_is_err
        async for ev in query(prompt=user, options=options):
            # ResultMessage carries the per-call cost / error flag.
            if type(ev).__name__ == "ResultMessage":
                rc = getattr(ev, "total_cost_usd", None)
                if isinstance(rc, (int, float)):
                    result_cost = float(rc)
                result_is_err = bool(getattr(ev, "is_error", False))
                continue
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

    try:
        await asyncio.wait_for(_drive(), timeout=_CALL_TIMEOUT_S)
    except TimeoutError:
        logger.warning(f"subscription_llm[{backend}]: timed out after {_CALL_TIMEOUT_S:.0f}s")
        return None, True
    except Exception as e:
        logger.warning(f"subscription_llm[{backend}]: {e}")
        return None, True

    text = "".join(chunks).strip()
    logger.info(
        "agent.usage session=single_shot backend={b} model={m} turns=1 cost_usd={c} is_error={e}",
        b=backend, m=resolved,
        c=f"{result_cost:.6f}" if result_cost is not None else None,
        e=result_is_err,
    )
    return (text or None), bool(result_is_err)


async def call_subscription(
    *,
    user: str,
    system: str | None = None,
    model: str | None = None,
    max_tokens: int = 800,
    temperature: float = 0.2,
) -> str | None:
    """Send one prompt through the active LLM backend; return text or None.

    No tools, no MCP, no permission prompts — single-shot text generation.
    Honors the Bedrock→subscription failover policy (see backend section above):
    in bedrock mode the call tries Bedrock and, once demoted, the subscription —
    so a single call can deliver the failover result. max_tokens / temperature
    are informational (the SDK doesn't expose them today).
    """
    if not subscription_enabled():
        return None
    for backend in _plan_backends():
        if backend == "subscription" and breaker_is_open():
            continue
        text, is_err = await _single_shot_once(
            backend, user=user, system=system, model=model
        )
        ok = (not is_err) and text is not None
        if backend == "bedrock":
            _record_bedrock(ok)
        else:
            _breaker_record(is_err)
        if ok:
            return text
    return None


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
    # Streaming loop: pick ONE backend up-front from the failover plan (we can't
    # swap backends mid-stream). Failover to the other backend happens on the
    # NEXT call — fine, since these agent loops recur on a schedule.
    plan = _plan_backends()
    if not plan:
        return
    backend = plan[0]
    if backend == "subscription" and breaker_is_open():
        return

    try:
        from claude_agent_sdk import ClaudeAgentOptions, query
    except ImportError as e:
        logger.warning(f"claude_agent_sdk import failed: {e}")
        return

    from matrix_shared.agent_runtime.runtime import run_agent_stream

    resolved_model = _resolve_model(model, backend)
    options = ClaudeAgentOptions(
        system_prompt=system or "",
        model=resolved_model,
        permission_mode="acceptEdits",
        mcp_servers=mcp_servers or {},
        allowed_tools=allowed_tools or [],
        disallowed_tools=disallowed_tools or [],
        setting_sources=[],
        env=_backend_options_env(backend),
    )

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
                "agent.usage session={s} backend={b} model={m} turns={t} "
                "cost_usd={c} is_error={e}{x}",
                s=session_id, b=backend, m=resolved_model, t=turns,
                c=f"{cost:.6f}" if isinstance(cost, (int, float)) else cost,
                e=is_err, x=extras,
            )
            payload.setdefault("model", resolved_model)
            if backend == "bedrock":
                _record_bedrock(ok=not is_err)
            else:
                _breaker_record(is_err)
        yield ev

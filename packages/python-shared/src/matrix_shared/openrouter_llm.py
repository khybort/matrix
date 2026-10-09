"""OpenRouter backend — OpenAI-compatible chat completions with tool calling.

Why: free-tier models (`:free` suffix) let every agent keep an LLM path when
the Claude subscription token is missing, rate-limited or the breaker is
open. Position in the failover plan (subscription_llm._plan_backends):

    MATRIX_LLM_BACKEND=openrouter → openrouter primary, subscription fallback
    default                        → subscription primary, openrouter fallback
    → deterministic rule path when neither answers.

Contract mirrors the other backends: single-shot returns (text, is_error);
the agent loop yields `AgentEvent`s (assistant_text / tool_use / tool_result
/ result) and drives the tool registry itself via OpenAI function calling,
so the same read/write/risk-gated belts work unchanged.

Free models change often. Tier defaults below are overridable with
`MATRIX_OPENROUTER_MODEL_<HAIKU|SONNET|OPUS>`; on a 404/400 model error the
call walks `MATRIX_OPENROUTER_FALLBACKS`. `make openrouter-models` lists what
is currently free. 429s open a short per-process cooldown so the 15s
decision loop never queues on a throttled provider.
"""

from __future__ import annotations

import os
import time
from collections.abc import AsyncIterator
from contextlib import nullcontext
from typing import Any

import httpx
import orjson
from loguru import logger

from matrix_shared import usage_ledger
from matrix_shared.agent_runtime.runtime import AgentEvent
from matrix_shared.agent_runtime.tool import ToolRegistry

API_URL = os.environ.get("OPENROUTER_API_URL", "https://openrouter.ai/api/v1/chat/completions")
MODELS_URL = os.environ.get("OPENROUTER_MODELS_URL", "https://openrouter.ai/api/v1/models")

# `or`, not a get() default: compose passes these as `${VAR:-}`, i.e. set but
# empty, which made every model id "" and the fallback list empty.
OPENROUTER_IDS = {
    "haiku": os.environ.get("MATRIX_OPENROUTER_MODEL_HAIKU") or "google/gemini-2.0-flash-exp:free",
    "sonnet": os.environ.get("MATRIX_OPENROUTER_MODEL_SONNET") or "deepseek/deepseek-chat-v3-0324:free",
    "opus": os.environ.get("MATRIX_OPENROUTER_MODEL_OPUS") or "qwen/qwen3-235b-a22b:free",
}
FALLBACK_MODELS: tuple[str, ...] = tuple(
    m.strip() for m in (
        os.environ.get("MATRIX_OPENROUTER_FALLBACKS")
        or "meta-llama/llama-3.3-70b-instruct:free,mistralai/mistral-small-3.1-24b-instruct:free"
    ).split(",") if m.strip()
)
_TIMEOUT_S = float(os.environ.get("MATRIX_OPENROUTER_TIMEOUT_S", "60"))
_COOLDOWN_S = float(os.environ.get("MATRIX_OPENROUTER_429_COOLDOWN_S", "90"))
_MAX_TOKENS = int(os.environ.get("MATRIX_OPENROUTER_MAX_TOKENS", "1500"))

_cooldown_until = 0.0
_last_error: str | None = None


def openrouter_enabled() -> bool:
    return bool(os.environ.get("OPENROUTER_API_KEY", "").strip())


def openrouter_primary() -> bool:
    return os.environ.get("MATRIX_LLM_BACKEND", "").strip().lower() == "openrouter"


def openrouter_ready() -> bool:
    """Configured and not cooling down after a 429."""
    return openrouter_enabled() and time.monotonic() >= _cooldown_until


def openrouter_state() -> dict[str, Any]:
    return {
        "enabled": openrouter_enabled(),
        "cooldown_remaining_s": max(0.0, _cooldown_until - time.monotonic()),
        "last_error": _last_error,
        "models": dict(OPENROUTER_IDS),
    }


def resolve_openrouter_model(model: str | None, tier_of: dict[str, str] | None = None) -> str:
    """Map a tier token / Anthropic id to the OpenRouter model for that tier.
    Unknown ids that already look like OpenRouter ids (`vendor/model`) pass through."""
    if model and "/" in model:
        return model
    tier = (tier_of or {}).get(model or "", None) or (model if model in OPENROUTER_IDS else None)
    return OPENROUTER_IDS.get(tier or "sonnet", OPENROUTER_IDS["sonnet"])


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {os.environ.get('OPENROUTER_API_KEY', '').strip()}",
        "HTTP-Referer": os.environ.get("OPENROUTER_REFERER", "https://github.com/matrix-local"),
        "X-Title": os.environ.get("OPENROUTER_APP_TITLE", "Matrix"),
        "Content-Type": "application/json",
    }


_JSON_TYPES = {str: "string", int: "integer", float: "number", bool: "boolean", list: "array", dict: "object"}


def tool_to_openai(name: str, description: str, input_schema: dict[str, Any]) -> dict[str, Any]:
    """Convert the registry's `{arg: pytype}` shorthand (or a JSON schema) to an
    OpenAI function definition."""
    if isinstance(input_schema, dict) and input_schema.get("type") == "object":
        params = input_schema
    else:
        props = {}
        for arg, typ in (input_schema or {}).items():
            if isinstance(typ, dict):
                props[arg] = typ
            else:
                props[arg] = {"type": _JSON_TYPES.get(typ, "string")}
                if typ is list:
                    props[arg]["items"] = {"type": "string"}
        params = {"type": "object", "properties": props}
    return {"type": "function", "function": {"name": name, "description": description[:1024], "parameters": params}}


def _record(status: int | None, err: str | None) -> None:
    global _cooldown_until, _last_error
    _last_error = err
    if status == 429:
        _cooldown_until = time.monotonic() + _COOLDOWN_S
        logger.warning(f"openrouter: 429 — cooling down {_COOLDOWN_S:.0f}s")


async def _chat(
    messages: list[dict[str, Any]], *, model: str, tools: list[dict] | None = None,
    max_tokens: int = _MAX_TOKENS, temperature: float = 0.2, client: httpx.AsyncClient | None = None,
) -> tuple[dict[str, Any] | None, int | None, str | None]:
    """One completions request with model fallback. Returns (message, status, error)."""
    candidates = [model] + [m for m in FALLBACK_MODELS if m != model]
    body_base: dict[str, Any] = {"messages": messages, "max_tokens": max_tokens, "temperature": temperature}
    if tools:
        body_base["tools"] = tools
        body_base["tool_choice"] = "auto"
    own = client is None
    client = client or httpx.AsyncClient(timeout=_TIMEOUT_S)
    try:
        last_status, last_err = None, None
        for cand in candidates:
            try:
                r = await client.post(API_URL, headers=_headers(), json={**body_base, "model": cand})
            except httpx.HTTPError as e:
                last_status, last_err = None, f"{cand}: {e}"
                continue
            last_status = r.status_code
            if r.status_code == 429:
                _record(429, f"{cand}: rate limited")
                return None, 429, "rate limited"
            if r.status_code in (400, 404) and "model" in r.text.lower():
                last_err = f"{cand}: {r.text[:160]}"
                logger.info(f"openrouter: {cand} unavailable ({r.status_code}); trying fallback")
                continue
            if r.status_code >= 400:
                last_err = f"{cand}: HTTP {r.status_code} {r.text[:160]}"
                if r.status_code >= 500:
                    continue
                break
            data = r.json()
            choice = (data.get("choices") or [{}])[0]
            msg = choice.get("message") or {}
            msg["_model"] = data.get("model", cand)
            msg["_usage"] = data.get("usage") or {}
            _record(200, None)
            return msg, 200, None
        _record(last_status, last_err)
        return None, last_status, last_err
    finally:
        if own:
            await client.aclose()


async def openrouter_single_shot(
    *, user: str, system: str | None, model: str | None, tier_of: dict[str, str] | None = None,
    client: httpx.AsyncClient | None = None,
) -> tuple[str | None, bool]:
    if not openrouter_ready():
        return None, True
    resolved = resolve_openrouter_model(model, tier_of)
    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})
    msg, status, err = await _chat(messages, model=resolved, client=client)
    if msg is None:
        logger.warning(f"openrouter single-shot failed: {status} {err}")
        return None, True
    text = (msg.get("content") or "").strip() if isinstance(msg.get("content"), str) else ""
    logger.info(
        "agent.usage session=single_shot backend=openrouter model={m} turns=1 cost_usd=0 is_error=False tokens={t}",
        m=msg.get("_model"), t=(msg.get("_usage") or {}).get("total_tokens"),
    )
    usage_ledger.record(session="single_shot", backend="openrouter", model=msg.get("_model"), turns=1,
                        cost_usd=0.0, is_error=False)
    return (text or None), False


async def openrouter_agent_stream(
    *,
    prompt: str,
    system: str | None = None,
    model: str | None = None,
    tool_registry: ToolRegistry | None = None,
    max_turns: int = 12,
    session_id: str = "agent",
    limiter: Any | None = None,
    can_use_tool: Any | None = None,
    tier_of: dict[str, str] | None = None,
    client: httpx.AsyncClient | None = None,
) -> AsyncIterator[AgentEvent]:
    """Multi-step tool loop over the registry via OpenAI function calling."""
    if not openrouter_ready():
        return
    resolved = resolve_openrouter_model(model, tier_of)
    tools = [tool_to_openai(t.name, t.description, t.input_schema) for t in (tool_registry.all() if tool_registry else [])]
    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    turns = 0
    slot = limiter.slot() if limiter is not None else nullcontext()
    own = client is None
    client = client or httpx.AsyncClient(timeout=_TIMEOUT_S)
    try:
        async with slot:
            while True:
                msg, status, err = await _chat(messages, model=resolved, tools=tools or None, client=client)
                if msg is None:
                    yield AgentEvent("result", {"total_cost_usd": 0.0, "num_turns": turns, "is_error": True,
                                                "reason": f"openrouter {status}: {err}", "model": resolved})
                    return
                content = msg.get("content")
                if isinstance(content, str) and content.strip():
                    yield AgentEvent("assistant_text", {"text": content})
                calls = msg.get("tool_calls") or []
                messages.append({k: v for k, v in msg.items() if not k.startswith("_")})
                if not calls:
                    yield AgentEvent("result", {"total_cost_usd": 0.0, "num_turns": turns, "is_error": False,
                                                "model": msg.get("_model", resolved)})
                    logger.info("agent.usage session={s} backend=openrouter model={m} turns={t} cost_usd=0 is_error=False",
                                s=session_id, m=msg.get("_model", resolved), t=turns)
                    return
                for call in calls:
                    turns += 1
                    if turns >= max_turns:
                        yield AgentEvent("result", {"total_cost_usd": 0.0, "num_turns": turns, "is_error": True,
                                                    "reason": "max_turns", "model": resolved})
                        return
                    fn = (call.get("function") or {})
                    name = fn.get("name", "")
                    try:
                        args = orjson.loads(fn.get("arguments") or "{}")
                    except orjson.JSONDecodeError:
                        args = {}
                    yield AgentEvent("tool_use", {"name": name, "params": args, "id": call.get("id", "")})
                    allowed = True
                    if can_use_tool is not None:
                        res = can_use_tool(name, args)
                        allowed = await res if hasattr(res, "__await__") else bool(res)
                    if not allowed or tool_registry is None or name not in tool_registry:
                        result_text, is_error = f"ERROR: tool {name!r} not permitted", True
                    else:
                        try:
                            out = await tool_registry.get(name).handler(args)
                            result_text = "".join(
                                str(c.get("text", "")) for c in (out.get("content") or []) if isinstance(c, dict)
                            ) or orjson.dumps(out, default=str).decode()
                            is_error = bool(out.get("is_error", False))
                        except Exception as e:  # noqa: BLE001 — tool failure is data for the model
                            result_text, is_error = f"ERROR: {e}", True
                    yield AgentEvent("tool_result", {"tool_use_id": call.get("id", ""), "content": result_text[:8000],
                                                     "is_error": is_error})
                    messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": result_text[:8000]})
    finally:
        if own:
            await client.aclose()


async def list_free_models(client: httpx.AsyncClient | None = None) -> list[dict[str, Any]]:
    own = client is None
    client = client or httpx.AsyncClient(timeout=_TIMEOUT_S)
    try:
        r = await client.get(MODELS_URL, headers=_headers())
        r.raise_for_status()
        out = []
        for m in r.json().get("data", []):
            if str(m.get("id", "")).endswith(":free"):
                params = m.get("supported_parameters") or []
                out.append({"id": m["id"], "context": m.get("context_length"), "tools": "tools" in params})
        return sorted(out, key=lambda x: (not x["tools"], x["id"]))
    finally:
        if own:
            await client.aclose()

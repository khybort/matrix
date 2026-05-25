"""Shared Anthropic LLM helper.

One small async function — `call_claude` — used by every LLM call site
(agent decisions, graph entity+relation extraction, hourly synthesis,
reflection rationale). The key + default model live in `config.py`;
callers stay tiny.

Three return shapes:
    text mode (default)       — raw string content
    json mode                 — parsed dict (caller knows the schema)
    None on transport error   — caller falls back gracefully

This keeps Vercel AI Gateway as a swappable detail rather than a hard
dependency; if you want to route through the Gateway later, change the
endpoint here and nothing else has to move.
"""

from __future__ import annotations

import httpx
import orjson
from loguru import logger

from matrix_shared.config import get_settings

API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_TIMEOUT_S = 35.0


def llm_enabled() -> bool:
    """True when an Anthropic key is configured (env or hardcoded fallback)."""
    return bool(get_settings().anthropic_api_key)


async def call_claude(
    *,
    user: str,
    system: str | None = None,
    model: str | None = None,
    max_tokens: int = 800,
    temperature: float = 0.2,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> str | None:
    """Send one Messages-API request; return text content or None on failure.

    None means "no usable response" — caller should fall back deterministically.
    HTTP errors, JSON parsing issues, empty/malformed content all collapse
    to None so call sites can stay simple.
    """
    settings = get_settings()
    api_key = settings.anthropic_api_key
    if not api_key:
        return None

    body: dict = {
        "model": model or settings.anthropic_model_default,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [{"role": "user", "content": user}],
    }
    if system:
        body["system"] = system

    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(
                API_URL,
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": ANTHROPIC_VERSION,
                    "content-type": "application/json",
                },
                content=orjson.dumps(body),
            )
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning(f"anthropic: {e}")
        return None

    # Anthropic returns content as a list of blocks; concatenate text blocks.
    try:
        blocks = data["content"]
    except (KeyError, TypeError):
        logger.warning(f"anthropic: unexpected response shape: {data!r}")
        return None
    chunks: list[str] = []
    for b in blocks:
        if isinstance(b, dict) and b.get("type") == "text":
            chunks.append(str(b.get("text", "")))
    text = "".join(chunks).strip()
    return text or None


async def call_claude_json(
    *,
    user: str,
    system: str | None = None,
    model: str | None = None,
    max_tokens: int = 1200,
    temperature: float = 0.2,
) -> dict | None:
    """Convenience: call_claude + parse the response as JSON, tolerating
    code-fenced output (```json … ``` blocks). Returns None on any failure.
    """
    text = await call_claude(
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
        logger.warning(f"anthropic: response not valid JSON: {text[:200]}")
        return None
    return parsed if isinstance(parsed, dict) else None

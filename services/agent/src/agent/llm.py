"""Optional LLM-based decision engine via Vercel AI Gateway.

If AI_GATEWAY_API_KEY is unset, callers fall back to rule-based decisions.
The gateway accepts an OpenAI-compatible chat-completions interface; model
strings are of the form "anthropic/claude-haiku-4-5".
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
import orjson
from loguru import logger

from matrix_shared import get_settings

GATEWAY_URL = "https://ai-gateway.vercel.sh/v1/chat/completions"
DEFAULT_MODEL = "anthropic/claude-haiku-4-5"
HTTP_TIMEOUT_S = 25.0


@dataclass(slots=True)
class LLMDecision:
    side: str  # long | short | hold
    confidence: float  # 0..1
    reasoning: str


def llm_enabled() -> bool:
    return bool(get_settings().ai_gateway_api_key)


async def call_llm_decision(prompt_user: str, *, model: str = DEFAULT_MODEL) -> LLMDecision | None:
    """Send prompt; expect JSON object with side/confidence/reasoning.

    Returns None on any failure — caller falls back to rule-based.
    """
    api_key = get_settings().ai_gateway_api_key
    if not api_key:
        return None

    body = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a quantitative trading agent for short-horizon crypto "
                    "perpetual trades. Given the current state, decide LONG, SHORT, "
                    "or HOLD. Respond with ONLY a JSON object: "
                    '{"side":"long|short|hold","confidence":0.0-1.0,"reasoning":"..."}. '
                    "Be cautious — bias toward HOLD when signals conflict. Confidence "
                    "should reflect signal strength, not enthusiasm."
                ),
            },
            {"role": "user", "content": prompt_user},
        ],
        "max_tokens": 200,
        "temperature": 0.2,
    }
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_S) as client:
            resp = await client.post(
                GATEWAY_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                content=orjson.dumps(body),
            )
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning(f"llm gateway error: {e}")
        return None

    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        logger.warning(f"llm response unexpected shape: {e}")
        return None

    text = text.strip()
    if text.startswith("```"):
        # tolerate code-fenced JSON
        lines = [ln for ln in text.splitlines() if not ln.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        parsed = orjson.loads(text)
    except orjson.JSONDecodeError:
        logger.warning(f"llm response not valid JSON: {text[:200]}")
        return None

    side = str(parsed.get("side", "hold")).lower()
    if side not in {"long", "short", "hold"}:
        return None
    try:
        confidence = float(parsed.get("confidence", 0.0))
    except (ValueError, TypeError):
        return None
    confidence = max(0.0, min(1.0, confidence))
    reasoning = str(parsed.get("reasoning", ""))[:1000]
    return LLMDecision(side=side, confidence=confidence, reasoning=reasoning)

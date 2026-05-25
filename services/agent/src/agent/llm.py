"""Optional LLM-based decision engine via Anthropic Messages API.

Routes through `matrix_shared.llm.call_claude_json`. When no key is
configured (or the call fails), callers fall back to rule-based decisions.
"""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger

from matrix_shared import call_claude_json, llm_enabled as _llm_enabled

DEFAULT_MODEL = "claude-haiku-4-5"


@dataclass(slots=True)
class LLMDecision:
    side: str        # long | short | hold
    confidence: float  # 0..1
    reasoning: str


def llm_enabled() -> bool:
    return _llm_enabled()


async def call_llm_decision(prompt_user: str, *, model: str = DEFAULT_MODEL) -> LLMDecision | None:
    """Send the decision prompt; expect JSON with side/confidence/reasoning.

    Returns None on any failure — caller falls back to rule-based.
    """
    system = (
        "You are a quantitative trading agent for short-horizon crypto "
        "perpetual trades. Given the current state, decide LONG, SHORT, "
        "or HOLD. Respond with ONLY a JSON object: "
        '{"side":"long|short|hold","confidence":0.0-1.0,"reasoning":"..."}. '
        "Be cautious — bias toward HOLD when signals conflict. Confidence "
        "should reflect signal strength, not enthusiasm."
    )
    parsed = await call_claude_json(
        system=system, user=prompt_user, model=model, max_tokens=500, temperature=0.2,
    )
    if parsed is None:
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

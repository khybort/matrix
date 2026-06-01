"""Optional LLM-based decision engine via Anthropic Messages API.

Routes through `matrix_shared.llm.call_claude_json`. When no key is
configured (or the call fails), callers fall back to rule-based decisions.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import orjson

from matrix_shared import call_claude, call_claude_json
from matrix_shared import llm_enabled as _llm_enabled
# Decision override follows the project default tier (Haiku by default — see
# `make llm-haiku`/`llm-sonnet`). This call shares the 15s loop's rate budget,
# so the cheap tier is the profit-aligned choice here.
from matrix_shared.subscription_llm import DEFAULT_MODEL


@dataclass(slots=True)
class LLMDecision:
    side: str        # long | short | hold
    confidence: float  # 0..1
    reasoning: str


def llm_enabled() -> bool:
    return _llm_enabled()


_BATCH_CHUNK = 4  # symbols per LLM call — keeps prompts within 45s timeout

_BATCH_SYSTEM = (
    "You are a quantitative trading agent for short-horizon crypto perpetual trades. "
    "For each symbol below, decide LONG, SHORT, or HOLD. "
    'Return ONLY a JSON array: [{"symbol":"BTCUSDT","side":"long|short|hold",'
    '"confidence":0.0-1.0,"reasoning":"..."}]. '
    "Be cautious — bias toward HOLD when signals conflict. One object per symbol, "
    "in the same order they appear."
)


def _parse_batch_text(text: str) -> dict[str, LLMDecision]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = "\n".join(
            ln for ln in stripped.splitlines() if not ln.strip().startswith("```")
        ).strip()
    try:
        items = orjson.loads(stripped)
    except Exception:
        return {}
    if not isinstance(items, list):
        return {}
    results: dict[str, LLMDecision] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        sym = str(item.get("symbol", ""))
        side = str(item.get("side", "hold")).lower()
        if not sym or side not in {"long", "short", "hold"}:
            continue
        try:
            conf = float(item.get("confidence", 0.0))
        except (ValueError, TypeError):
            conf = 0.0
        results[sym] = LLMDecision(
            side=side,
            confidence=max(0.0, min(1.0, conf)),
            reasoning=str(item.get("reasoning", ""))[:1000],
        )
    return results


async def call_llm_decisions_batch(
    prompts: list[tuple[str, str]],
    *,
    model: str = DEFAULT_MODEL,
    chunk_size: int = _BATCH_CHUNK,
) -> dict[str, LLMDecision]:
    """Batch LLM decisions: chunks run concurrently, each within the 45s timeout.

    Wall-clock ≈ slowest single chunk (~30s) regardless of symbol count.
    Absent symbols fall back to rule-based in the caller.
    """
    if not prompts:
        return {}

    async def _call_chunk(chunk: list[tuple[str, str]]) -> dict[str, LLMDecision]:
        sections = [f"=== {sym} ===\n{prompt}" for sym, prompt in chunk]
        text = await call_claude(
            system=_BATCH_SYSTEM,
            user="\n\n".join(sections),
            model=model,
            max_tokens=300 * len(chunk),
            temperature=0.2,
        )
        return _parse_batch_text(text) if text else {}

    chunks = [prompts[i : i + chunk_size] for i in range(0, len(prompts), chunk_size)]
    results = await asyncio.gather(*[_call_chunk(c) for c in chunks], return_exceptions=True)

    merged: dict[str, LLMDecision] = {}
    for r in results:
        if isinstance(r, dict):
            merged.update(r)
    return merged


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

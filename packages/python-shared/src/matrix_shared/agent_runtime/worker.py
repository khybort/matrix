"""Worker pattern — Haiku-side context distillation for tool outputs.

The orchestrator agent (Brain/Sonnet, sometimes Opus) should never see a raw
5000-word blob just to extract one fact from it. Tools that *could* return
something large delegate to this helper: Haiku compresses the blob into a
structured summary, and only the summary enters the orchestrator's context.

Architecturally:

    orchestrator (Opus/Sonnet)
        │  call tool
        ▼
    tool implementation
        ├─ fetch raw data
        ├─ haiku_distill(raw=..., instruction=...)
        └─ return {"summary": "...", ...}   ← orchestrator sees ONLY this

This is the *minimum-context delegation* the operator asked for: cheap model
does the bulk-text work, expensive model does the reasoning over distilled
inputs. No subagent spawn, no extra SDK session — just a nested single-shot
call to `subscription_llm.call_subscription`, which is rate-limit-free
(the loop's outer slot is unaffected).

Falls back to a deterministic truncated string when subscription is
unavailable so callers never get None — distillation is an *optimization*,
not a hard dependency.
"""

from __future__ import annotations

from typing import Any

import orjson

from matrix_shared.subscription_llm import (
    MODEL_HAIKU,
    call_subscription,
    subscription_enabled,
)

# Haiku 4.5 is the cheapest, fastest model in the current family. Tuned for
# format conversion / summarization / classification — exactly the worker
# shape. Pulls the model ID from subscription_llm so a Bedrock/Vertex
# operator's MATRIX_MODEL_HAIKU env override applies here automatically.
HAIKU_MODEL = MODEL_HAIKU

# Distillation output budget. Orchestrator sees at most this many tokens
# of summary regardless of raw input size.
DEFAULT_MAX_TOKENS = 600

# Deterministic fallback char budget (used when subscription is off / Haiku
# call fails). Roughly matches the model's max_tokens for parity.
DEFAULT_FALLBACK_CHARS = 800

_DISTILL_SYSTEM = (
    "You compress data into compact structured summaries for an orchestrator "
    "agent that will reason over the result. Be precise, use bullet points, "
    "drop filler. Never invent facts not present in the input. If the input "
    "is too sparse to summarize meaningfully, say so in one line."
)


def _deterministic_truncate(raw: Any, max_chars: int = DEFAULT_FALLBACK_CHARS) -> str:
    """Best-effort string-form fallback when Haiku is unavailable."""
    if isinstance(raw, str):
        s = raw
    else:
        s = orjson.dumps(raw, default=str).decode()
    if len(s) <= max_chars:
        return s
    return s[: max_chars - 1] + "…"


async def haiku_distill(
    *,
    raw: Any,
    instruction: str,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> str:
    """Compress `raw` to a structured summary via Haiku.

    `instruction` is the caller's spec for what kind of summary it needs
    (e.g. "5-8 bullets covering themes, mentioned tickers, sentiment").
    The orchestrator never sees `raw`; only the returned string lands in
    its context.

    Returns a deterministic truncated string when subscription is
    unavailable, so the calling tool always has something useful to
    return.
    """
    if not subscription_enabled():
        return _deterministic_truncate(raw)

    body = raw if isinstance(raw, str) else orjson.dumps(raw, default=str).decode()
    text = await call_subscription(
        system=_DISTILL_SYSTEM,
        user=f"Instruction: {instruction}\n\nDATA:\n{body}",
        model=HAIKU_MODEL,
        max_tokens=max_tokens,
        temperature=0.2,
    )
    if not text:
        return _deterministic_truncate(raw)
    return text

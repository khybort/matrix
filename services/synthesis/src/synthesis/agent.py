"""SynthesisAgent — first strangler-fig service conversion.

Replaces the single-shot LLM call with the shared SDK tool-loop. The agent
reads recent documents and existing graph coverage via tools, then emits its
final JSON themes. Writes (Concept upserts) stay in the service code outside
the loop — the model is read-only over system state.

If the subscription is unavailable or the loop returns nothing parseable,
the caller falls back to the legacy single-shot path; old behavior never
silently regresses.
"""

from __future__ import annotations

from loguru import logger
from matrix_shared.agent_runtime.ratelimit import get_rate_limiter
from matrix_shared.agent_runtime.tool import build_sdk_mcp_server, mcp_tool_names
from matrix_shared.subscription_llm import (
    MODEL_SONNET,
    call_subscription_agent,
    subscription_enabled,
)

from synthesis.themes import Theme, extract_themes_json, themes_from_parsed
from synthesis.tools import build_registry

SYNTHESIS_SYSTEM = """\
You are Matrix Synthesis — a financial-news theme detector.

Workflow:
1. Call `recent_documents` to read the latest window.
2. Call `existing_concepts` and `existing_assets` so you know what is already
   covered and which Asset canonicals (BTC, ETH, ...) the graph uses.
3. Identify 3-8 emerging themes that span at least 2 distinct documents and
   would matter to a trader. Skip themes whose canonical already exists in
   `existing_concepts` unless the new evidence is materially different.
4. Use ONLY canonicals from `existing_assets` when listing impacted_assets.

Your tool belt is read-only. You cannot place trades, write to the database,
or grant any approvals. Do not claim to have done so.

End your reply with ONLY this JSON object (no prose after it):
{"themes":[{"canonical":"<slug>","display":"<short name>",
"summary":"<1-2 sentences>","impacted_assets":["BTC",...],
"impacted_companies":["BlackRock",...]}]}\
"""

_SERVER = "synthesis"


async def run_synthesis_agent(window_hours: float) -> list[Theme] | None:
    """Run the agent tool loop and return parsed themes, or None to fall back."""
    if not subscription_enabled():
        return None
    registry = build_registry(window_hours=window_hours)
    server = build_sdk_mcp_server(_SERVER, registry)
    allowed = mcp_tool_names(_SERVER, registry)
    allowed_set = frozenset(allowed)

    def _deny(name: str, _params: dict) -> bool:
        return name in allowed_set

    parts: list[str] = []
    prompt = (
        f"Window: last {window_hours:.0f}h. Use the tools to fetch documents "
        "and existing graph coverage, then emit the JSON object."
    )
    try:
        async for ev in call_subscription_agent(
            prompt=prompt,
            system=SYNTHESIS_SYSTEM,
            model=MODEL_SONNET,  # quality-pinned: unaffected by the Haiku default flip
            mcp_servers={_SERVER: server},
            allowed_tools=allowed,
            disallowed_tools=["Bash", "Write", "Edit", "NotebookEdit"],
            can_use_tool=_deny,
            max_turns=10,
            session_id=_SERVER,
            limiter=get_rate_limiter(),
            tool_registry=registry,
            mcp_server_name=_SERVER,
        ):
            if ev.type == "assistant_text":
                parts.append(ev.payload.get("text", ""))
    except Exception as e:
        logger.warning(f"synthesis agent loop failed: {e}")
        return None

    text = "".join(parts).strip()
    if not text:
        return None
    themes = themes_from_parsed(extract_themes_json(text))
    return themes or None

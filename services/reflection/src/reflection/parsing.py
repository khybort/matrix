"""Pure parser/validator for mutation-proposal output (legacy LLM + agent).

Safety-critical: this module is the single chokepoint that strips risk-cap
fields the model might try to set. Both the legacy `llm_propose` and the new
`agent_propose` paths route their JSON through `parse_mutation_draft` so the
forbidden-fields list is enforced exactly once and is testable in isolation.

Risk caps belong to the Wallet model (max_position_pct, daily_loss_circuit_pct,
max_concurrent_positions, live_capital_cap_usd, live_execution_enabled) and
are off-limits to the reflection layer — see docs/TRADING.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import orjson

ALLOWED_PROPOSAL_TYPES = ("weight_tune", "threshold_change", "prompt_change")

# Wallet-owned fields the reflection layer must NEVER mutate. Stripped from
# after_params on every parse — defense in depth on top of the system prompt.
FORBIDDEN_RISK_FIELDS = frozenset(
    {
        "max_position_pct",
        "daily_loss_circuit_pct",
        "max_concurrent_positions",
        "live_capital_cap_usd",
        "live_execution_enabled",
    }
)

RATIONALE_MAX = 2000

_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


@dataclass(slots=True)
class MutationDraft:
    proposal_type: str
    before_params: dict[str, Any]
    after_params: dict[str, Any]
    rationale: str
    source: str  # "rule" | "llm" | "agent"


def extract_json_object(text: str | None) -> dict | None:
    """Pull the first JSON object out of `text`. Tolerates ```json fences and
    surrounding prose."""
    if not text:
        return None
    body = text.strip()
    if body.startswith("```"):
        body = "\n".join(
            ln for ln in body.splitlines() if not ln.strip().startswith("```")
        ).strip()
    for candidate in (body, _first_object(body)):
        if candidate is None:
            continue
        try:
            parsed = orjson.loads(candidate)
        except orjson.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _first_object(text: str) -> str | None:
    m = _OBJ_RE.search(text)
    return m.group(0) if m else None


def parse_mutation_draft(
    parsed: dict | None,
    *,
    current_params: dict[str, Any],
    source: str,
) -> MutationDraft | None:
    """Validate, strip forbidden fields, return a MutationDraft or None.

    None signals "no actionable proposal" — including the case where the model
    only attempted to mutate risk caps (after stripping those, nothing remains).
    """
    if not isinstance(parsed, dict) or not parsed:
        return None

    proposal_type = str(parsed.get("proposal_type", "")).strip()
    if proposal_type not in ALLOWED_PROPOSAL_TYPES:
        return None

    after = parsed.get("after_params")
    if not isinstance(after, dict) or not after:
        return None

    # Defense in depth: strip wallet-owned risk fields even if the prompt
    # explicitly forbade them. The model is not on the allow-path here.
    cleaned = {k: v for k, v in after.items() if k not in FORBIDDEN_RISK_FIELDS}
    if not cleaned:
        return None  # only forbidden fields remained → no actionable change

    rationale = str(parsed.get("rationale", ""))[:RATIONALE_MAX]
    return MutationDraft(
        proposal_type=proposal_type,
        before_params=current_params,
        after_params=cleaned,
        rationale=rationale,
        source=source,
    )

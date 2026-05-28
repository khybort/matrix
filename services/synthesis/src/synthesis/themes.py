"""Pure parser/validator for the synthesis themes payload.

The LLM (agent path or single-shot fallback) returns a JSON object describing
emerging themes; this module turns that into a clamped `list[Theme]` the
persister upserts as Concept nodes. No DB / LLM access here — keep the rest
of synthesis testable without either.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import orjson

MAX_THEMES = 8
CANONICAL_MAX = 96
DISPLAY_MAX = 200
SUMMARY_MAX = 1000
ASSET_MAX = 32
COMPANY_MAX = 128

_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


@dataclass(slots=True)
class Theme:
    canonical: str
    display: str
    summary: str
    impacted_assets: list[str] = field(default_factory=list)
    impacted_companies: list[str] = field(default_factory=list)


def extract_themes_json(text: str) -> dict | None:
    """Pull the first JSON object out of `text`. Tolerates ```json fences and
    leading/trailing prose."""
    if not text:
        return None
    # Strip code fences if present.
    body = text.strip()
    if body.startswith("```"):
        body = "\n".join(
            ln for ln in body.splitlines() if not ln.strip().startswith("```")
        ).strip()
    # Try the whole body first; if it's not a clean JSON object, grab the
    # first {...} substring (the model often surrounds JSON with prose).
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


def themes_from_parsed(parsed: dict | None) -> list[Theme]:
    """Validate + clamp the parsed payload into a Theme list."""
    if not isinstance(parsed, dict):
        return []
    raw = parsed.get("themes")
    if not isinstance(raw, list):
        return []
    out: list[Theme] = []
    for t in raw[:MAX_THEMES]:
        if not isinstance(t, dict):
            continue
        canonical = str(t.get("canonical", "")).strip()[:CANONICAL_MAX]
        if not canonical:
            continue
        display = str(t.get("display", canonical))[:DISPLAY_MAX]
        summary = str(t.get("summary", ""))[:SUMMARY_MAX]
        assets = [
            str(x).strip()[:ASSET_MAX]
            for x in (t.get("impacted_assets") or [])
            if isinstance(x, (str, int, float)) and str(x).strip()
        ]
        companies = [
            str(x).strip()[:COMPANY_MAX]
            for x in (t.get("impacted_companies") or [])
            if isinstance(x, (str, int, float)) and str(x).strip()
        ]
        out.append(
            Theme(
                canonical=canonical,
                display=display,
                summary=summary,
                impacted_assets=assets,
                impacted_companies=companies,
            )
        )
    return out

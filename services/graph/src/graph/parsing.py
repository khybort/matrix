"""Pure parser/validator for entity+relation extraction output.

Shared by the legacy single-shot `llm_extract` and the new tool-loop
`run_extract_agent`. No DB or LLM access — keep both paths thin and the
validation logic centrally tested.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import orjson

MAX_ENTITIES = 12
MAX_RELATIONS = 12
CANONICAL_MAX = 128
DISPLAY_MAX = 200

ALLOWED_ENTITY_TYPES = ("Asset", "Company", "Person", "Event", "Concept")
ALLOWED_EDGE_TYPES = (
    "IMPACTS",
    "EMPLOYS",
    "ANNOUNCES",
    "OWNS",
    "REGULATES",
    "PARTNERS_WITH",
    "COMPETES_WITH",
    "PARTICIPATES_IN",
    "RELATED_TO",
)

_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


@dataclass(slots=True)
class Entity:
    type: str
    canonical: str
    display: str
    props: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class Relation:
    source_type: str
    source_canonical: str
    edge_type: str
    target_type: str
    target_canonical: str


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


def parse_extraction(parsed: dict | None) -> tuple[list[Entity], list[Relation]]:
    """Validate + clamp a parsed extraction payload.

    Drops relations whose endpoints weren't emitted as entities (so the graph
    stays honest — the LLM occasionally hallucinates endpoints).
    """
    if not isinstance(parsed, dict):
        return [], []

    entities: list[Entity] = []
    seen_keys: set[tuple[str, str]] = set()
    raw_entities = parsed.get("entities")
    if isinstance(raw_entities, list):
        for e in raw_entities[:MAX_ENTITIES]:
            if not isinstance(e, dict):
                continue
            t = str(e.get("type", "")).strip()
            if t not in ALLOWED_ENTITY_TYPES:
                continue
            canonical = str(e.get("canonical", "")).strip()[:CANONICAL_MAX]
            if not canonical:
                continue
            if (t, canonical) in seen_keys:
                continue
            display = str(e.get("display", canonical))[:DISPLAY_MAX]
            seen_keys.add((t, canonical))
            entities.append(Entity(type=t, canonical=canonical, display=display))

    relations: list[Relation] = []
    raw_rels = parsed.get("relations")
    if isinstance(raw_rels, list):
        for r in raw_rels[:MAX_RELATIONS]:
            if not isinstance(r, dict):
                continue
            edge = str(r.get("edge", "")).strip().upper()
            if edge not in ALLOWED_EDGE_TYPES:
                continue
            src = r.get("source") if isinstance(r.get("source"), dict) else {}
            tgt = r.get("target") if isinstance(r.get("target"), dict) else {}
            st = str(src.get("type", "")).strip()
            sc = str(src.get("canonical", "")).strip()[:CANONICAL_MAX]
            tt = str(tgt.get("type", "")).strip()
            tc = str(tgt.get("canonical", "")).strip()[:CANONICAL_MAX]
            if st not in ALLOWED_ENTITY_TYPES or tt not in ALLOWED_ENTITY_TYPES:
                continue
            if not sc or not tc:
                continue
            if (st, sc) not in seen_keys or (tt, tc) not in seen_keys:
                continue
            relations.append(
                Relation(
                    source_type=st,
                    source_canonical=sc,
                    edge_type=edge,
                    target_type=tt,
                    target_canonical=tc,
                )
            )

    return entities, relations

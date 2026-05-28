"""Pure parser for the extraction agent's output.

The single-shot LLM path and the new agent path return the same JSON shape
(entities + relations); this module validates and clamps it. Tested
DB- and LLM-free.
"""

from __future__ import annotations

from graph.parsing import (
    Entity,
    Relation,
    extract_json_object,
    parse_extraction,
)


def test_extract_json_object_strips_fences():
    text = '```json\n{"entities":[],"relations":[]}\n```'
    assert extract_json_object(text) == {"entities": [], "relations": []}


def test_extract_json_object_returns_none_for_garbage():
    assert extract_json_object("nothing structured here") is None


def test_extract_json_object_recovers_embedded():
    text = 'Sure, here goes:\n{"entities":[{"type":"Asset","canonical":"BTC"}]}\n'
    out = extract_json_object(text)
    assert isinstance(out, dict) and out["entities"][0]["canonical"] == "BTC"


def test_parse_extraction_keeps_valid_entity_and_drops_unknown_type():
    parsed = {
        "entities": [
            {"type": "Asset", "canonical": "BTC", "display": "Bitcoin"},
            {"type": "Mineral", "canonical": "Gold"},          # bad type
            {"type": "Company", "canonical": "  "},             # empty
            "not a dict",                                       # garbage
        ],
        "relations": [],
    }
    entities, relations = parse_extraction(parsed)
    assert [(e.type, e.canonical) for e in entities] == [("Asset", "BTC")]
    assert relations == []


def test_parse_extraction_caps_entities_at_twelve():
    parsed = {
        "entities": [
            {"type": "Asset", "canonical": f"A{i}", "display": f"A{i}"}
            for i in range(20)
        ],
        "relations": [],
    }
    entities, _ = parse_extraction(parsed)
    assert len(entities) == 12


def test_parse_extraction_drops_relation_with_unknown_edge_or_orphan_endpoint():
    parsed = {
        "entities": [
            {"type": "Company", "canonical": "BlackRock"},
            {"type": "Asset", "canonical": "BTC"},
        ],
        "relations": [
            {
                "source": {"type": "Company", "canonical": "BlackRock"},
                "edge": "OWNS",  # valid
                "target": {"type": "Asset", "canonical": "BTC"},
            },
            {
                "source": {"type": "Company", "canonical": "BlackRock"},
                "edge": "LIKES",  # invalid edge
                "target": {"type": "Asset", "canonical": "BTC"},
            },
            {
                "source": {"type": "Company", "canonical": "Ghost"},  # not emitted
                "edge": "OWNS",
                "target": {"type": "Asset", "canonical": "BTC"},
            },
        ],
    }
    _entities, relations = parse_extraction(parsed)
    assert [(r.source_canonical, r.edge_type, r.target_canonical) for r in relations] == [
        ("BlackRock", "OWNS", "BTC")
    ]


def test_parse_extraction_uppercases_edge_type():
    parsed = {
        "entities": [
            {"type": "Asset", "canonical": "BTC"},
            {"type": "Asset", "canonical": "ETH"},
        ],
        "relations": [
            {
                "source": {"type": "Asset", "canonical": "BTC"},
                "edge": "related_to",
                "target": {"type": "Asset", "canonical": "ETH"},
            }
        ],
    }
    _entities, relations = parse_extraction(parsed)
    assert relations[0].edge_type == "RELATED_TO"


def test_parse_extraction_handles_bad_root():
    assert parse_extraction({}) == ([], [])
    assert parse_extraction({"entities": "oops"}) == ([], [])
    assert parse_extraction(None) == ([], [])  # type: ignore[arg-type]


def test_parse_extraction_deduplicates_entities():
    parsed = {
        "entities": [
            {"type": "Asset", "canonical": "BTC"},
            {"type": "Asset", "canonical": "BTC"},  # dup
        ],
        "relations": [],
    }
    entities, _ = parse_extraction(parsed)
    assert len(entities) == 1
    assert isinstance(entities[0], Entity)

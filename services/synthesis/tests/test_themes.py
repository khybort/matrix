"""Pure parser/validator for the synthesis agent's themes payload.

The agent (or single-shot fallback) returns a JSON object describing emerging
themes; this module turns that into a clamped list[Theme] the persister
upserts. Tested without DB or LLM.
"""

from __future__ import annotations

from synthesis.themes import Theme, extract_themes_json, themes_from_parsed


def test_extract_themes_strips_json_fences():
    text = '```json\n{"themes":[]}\n```'
    assert extract_themes_json(text) == {"themes": []}


def test_extract_themes_handles_plain_json():
    assert extract_themes_json('{"themes":[{"canonical":"x"}]}') == {
        "themes": [{"canonical": "x"}]
    }


def test_extract_themes_returns_none_for_non_json():
    assert extract_themes_json("here is what I think:") is None


def test_extract_themes_finds_embedded_object():
    """The model often wraps the JSON in a sentence; we recover it."""
    text = 'Here is the result:\n{"themes":[{"canonical":"a"}]}\nLet me know.'
    parsed = extract_themes_json(text)
    assert parsed == {"themes": [{"canonical": "a"}]}


def test_themes_from_parsed_builds_clamped_list():
    parsed = {
        "themes": [
            {
                "canonical": "etf_approval",
                "display": "ETF Approval Wave",
                "summary": "Multiple spot ETFs approved.",
                "impacted_assets": ["BTC", "ETH"],
                "impacted_companies": ["BlackRock"],
            }
        ]
    }
    out = themes_from_parsed(parsed)
    assert len(out) == 1
    t = out[0]
    assert isinstance(t, Theme)
    assert t.canonical == "etf_approval"
    assert t.impacted_assets == ["BTC", "ETH"]
    assert t.impacted_companies == ["BlackRock"]


def test_themes_from_parsed_caps_at_eight():
    parsed = {"themes": [{"canonical": f"t{i}", "display": f"T{i}"} for i in range(20)]}
    assert len(themes_from_parsed(parsed)) == 8


def test_themes_from_parsed_skips_invalid_entries():
    parsed = {
        "themes": [
            {"canonical": "good"},
            {"display": "no canonical"},      # missing canonical → drop
            "not a dict",                      # non-dict → drop
            {"canonical": "  "},               # whitespace canonical → drop
        ]
    }
    out = themes_from_parsed(parsed)
    assert [t.canonical for t in out] == ["good"]


def test_themes_from_parsed_handles_missing_or_bad_root():
    assert themes_from_parsed({}) == []
    assert themes_from_parsed({"themes": "oops"}) == []
    assert themes_from_parsed(None) == []  # type: ignore[arg-type]

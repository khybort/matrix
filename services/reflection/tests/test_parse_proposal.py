"""Pure parser/validator for mutation-proposal output.

The legacy single-shot path AND the new agent path emit the same JSON shape
({proposal_type, after_params, rationale}); both go through this validator.
Safety-critical: it MUST strip any risk-cap fields the model attempts to set.
"""

from __future__ import annotations

from reflection.parsing import (
    FORBIDDEN_RISK_FIELDS,
    MutationDraft,
    extract_json_object,
    parse_mutation_draft,
)

CURRENT = {"weights": {"news": "0.1"}, "signal_threshold": "0.18"}


def test_extract_json_object_strips_fences():
    assert extract_json_object('```json\n{"proposal_type":"weight_tune"}\n```') == {
        "proposal_type": "weight_tune"
    }


def test_extract_json_object_returns_none_for_garbage():
    assert extract_json_object("no json here") is None


def test_extract_json_object_recovers_embedded():
    text = 'Sure:\n{"proposal_type":"threshold_change","after_params":{"signal_threshold":"0.22"}}\n'
    parsed = extract_json_object(text)
    assert isinstance(parsed, dict) and parsed["proposal_type"] == "threshold_change"


def test_parse_valid_weight_tune():
    parsed = {
        "proposal_type": "weight_tune",
        "after_params": {"weights": {"news": "0.05"}},
        "rationale": "dampen news",
    }
    out = parse_mutation_draft(parsed, current_params=CURRENT, source="agent")
    assert isinstance(out, MutationDraft)
    assert out.proposal_type == "weight_tune"
    assert out.after_params == {"weights": {"news": "0.05"}}
    assert out.before_params == CURRENT
    assert out.source == "agent"


def test_parse_rejects_unknown_proposal_type():
    parsed = {
        "proposal_type": "lower_risk_cap",  # not allowed
        "after_params": {"max_position_pct": "0.5"},
    }
    assert parse_mutation_draft(parsed, current_params=CURRENT, source="agent") is None


def test_parse_rejects_empty_after_params():
    parsed = {"proposal_type": "weight_tune", "after_params": {}}
    assert parse_mutation_draft(parsed, current_params=CURRENT, source="agent") is None


def test_parse_strips_every_forbidden_risk_field():
    """The whole reason this parser exists. Risk caps are wallet-owned and the
    reflection layer is forbidden from touching them — even if the model tries."""
    after = {
        "weights": {"news": "0.05"},
        "max_position_pct": "0.99",
        "daily_loss_circuit_pct": "0.99",
        "max_concurrent_positions": 9999,
        "live_capital_cap_usd": "1000000",
        "live_execution_enabled": True,
    }
    parsed = {"proposal_type": "weight_tune", "after_params": after}
    out = parse_mutation_draft(parsed, current_params=CURRENT, source="agent")
    assert out is not None
    for f in FORBIDDEN_RISK_FIELDS:
        assert f not in out.after_params, f"forbidden field leaked: {f}"
    assert out.after_params == {"weights": {"news": "0.05"}}


def test_parse_returns_none_when_only_forbidden_fields_remain():
    """If everything in after_params was a risk cap, stripping leaves nothing —
    don't emit a no-op proposal."""
    parsed = {
        "proposal_type": "weight_tune",
        "after_params": {"max_position_pct": "0.5", "live_execution_enabled": True},
    }
    assert parse_mutation_draft(parsed, current_params=CURRENT, source="agent") is None


def test_parse_handles_bad_input():
    assert parse_mutation_draft(None, current_params=CURRENT, source="agent") is None
    assert parse_mutation_draft({}, current_params=CURRENT, source="agent") is None
    assert (
        parse_mutation_draft(
            {"proposal_type": "weight_tune", "after_params": "oops"},
            current_params=CURRENT,
            source="agent",
        )
        is None
    )


def test_parse_clamps_rationale_length():
    parsed = {
        "proposal_type": "weight_tune",
        "after_params": {"weights": {"news": "0.05"}},
        "rationale": "x" * 5000,
    }
    out = parse_mutation_draft(parsed, current_params=CURRENT, source="agent")
    assert out is not None and len(out.rationale) <= 2000

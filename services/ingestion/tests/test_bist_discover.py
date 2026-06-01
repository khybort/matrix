"""Tests for ingestion.bist.discover."""

from __future__ import annotations

import pytest

from ingestion.bist.discover import _parse_fintables_payload


def test_parse_fintables_payload_dedupes_and_normalises() -> None:
    data = [
        {"code": "thyao", "title": " Türk Hava Yolları ", "sectors": [10, 20]},
        {"code": "THYAO", "title": "duplicate"},
        {"code": "BAD", "title": "too short"},
        {"code": "12345", "title": "numeric"},
    ]
    out = _parse_fintables_payload(data)
    assert len(out) == 1
    assert out[0].symbol == "THYAO"
    assert out[0].name == "Türk Hava Yolları"
    assert out[0].sector == "10,20"


def test_parse_fintables_payload_rejects_non_array() -> None:
    with pytest.raises(ValueError, match="JSON array"):
        _parse_fintables_payload({"code": "THYAO"})

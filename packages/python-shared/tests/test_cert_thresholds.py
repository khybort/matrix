"""Cert eligibility env overrides."""

from __future__ import annotations

from decimal import Decimal

from matrix_shared.trading_safety import (
    DEFAULT_MIN_OBSERVATION_DAYS,
    cert_eligibility_thresholds,
)


def test_cert_thresholds_default_without_env(monkeypatch):
    for key in (
        "MATRIX_CERT_MIN_OBSERVATION_DAYS",
        "MATRIX_CERT_MIN_OUTCOMES",
        "MATRIX_CERT_MIN_WIN_RATE",
        "MATRIX_CERT_MIN_TOTAL_PNL_USD",
        "MATRIX_CERT_MAX_DRAWDOWN_PCT",
    ):
        monkeypatch.delenv(key, raising=False)
    t = cert_eligibility_thresholds()
    assert t["min_observation_days"] == DEFAULT_MIN_OBSERVATION_DAYS
    assert t["min_outcomes"] == 200


def test_cert_thresholds_env_override(monkeypatch):
    monkeypatch.setenv("MATRIX_CERT_MIN_OBSERVATION_DAYS", "0")
    monkeypatch.setenv("MATRIX_CERT_MIN_OUTCOMES", "20")
    monkeypatch.setenv("MATRIX_CERT_MIN_WIN_RATE", "0.35")
    monkeypatch.setenv("MATRIX_CERT_MIN_TOTAL_PNL_USD", "-50")
    monkeypatch.setenv("MATRIX_CERT_MAX_DRAWDOWN_PCT", "0.5")
    t = cert_eligibility_thresholds()
    assert t["min_observation_days"] == 0
    assert t["min_outcomes"] == 20
    assert t["min_win_rate"] == Decimal("0.35")

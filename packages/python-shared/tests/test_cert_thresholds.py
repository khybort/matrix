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


# --- mainnet refusal ------------------------------------------------------

_CERT_KEYS = (
    "MATRIX_CERT_MIN_OBSERVATION_DAYS",
    "MATRIX_CERT_MIN_OUTCOMES",
    "MATRIX_CERT_MIN_WIN_RATE",
    "MATRIX_CERT_MIN_TOTAL_PNL_USD",
    "MATRIX_CERT_MAX_DRAWDOWN_PCT",
)


def _clear(monkeypatch):
    for key in _CERT_KEYS + ("BYBIT_TESTNET", "ALPACA_PAPER"):
        monkeypatch.delenv(key, raising=False)


def test_is_mainnet_false_by_default(monkeypatch):
    from matrix_shared.trading_safety import is_mainnet

    _clear(monkeypatch)
    assert is_mainnet() is False
    monkeypatch.setenv("BYBIT_TESTNET", "true")
    assert is_mainnet() is False


def test_is_mainnet_when_any_venue_is_live(monkeypatch):
    from matrix_shared.trading_safety import is_mainnet

    _clear(monkeypatch)
    monkeypatch.setenv("BYBIT_TESTNET", "false")
    assert is_mainnet() is True
    _clear(monkeypatch)
    monkeypatch.setenv("ALPACA_PAPER", "false")
    assert is_mainnet() is True


def test_cert_overrides_active_lists_set_keys(monkeypatch):
    from matrix_shared.trading_safety import cert_overrides_active

    _clear(monkeypatch)
    assert cert_overrides_active() == []
    monkeypatch.setenv("MATRIX_CERT_MIN_OUTCOMES", "20")
    monkeypatch.setenv("MATRIX_CERT_MIN_WIN_RATE", "")  # empty = unset
    assert cert_overrides_active() == ["MATRIX_CERT_MIN_OUTCOMES"]


def test_mainnet_ignores_env_overrides(monkeypatch):
    """On mainnet the relaxed thresholds must NOT apply — defaults win."""
    _clear(monkeypatch)
    monkeypatch.setenv("BYBIT_TESTNET", "false")
    monkeypatch.setenv("MATRIX_CERT_MIN_OBSERVATION_DAYS", "0")
    monkeypatch.setenv("MATRIX_CERT_MIN_OUTCOMES", "20")
    t = cert_eligibility_thresholds()
    assert t["min_observation_days"] == DEFAULT_MIN_OBSERVATION_DAYS
    assert t["min_outcomes"] == 200


def test_mainnet_refusal_reasons(monkeypatch):
    from matrix_shared.trading_safety import mainnet_refusal_reasons

    _clear(monkeypatch)
    monkeypatch.setenv("MATRIX_CERT_MIN_OUTCOMES", "20")
    assert mainnet_refusal_reasons() == []  # testnet: overrides tolerated
    monkeypatch.setenv("BYBIT_TESTNET", "false")
    reasons = mainnet_refusal_reasons()
    assert len(reasons) == 1
    assert "MATRIX_CERT_MIN_OUTCOMES" in reasons[0]
    monkeypatch.delenv("MATRIX_CERT_MIN_OUTCOMES")
    assert mainnet_refusal_reasons() == []


def test_relaxed_cert_is_flagged_and_rejected_on_mainnet(monkeypatch):
    """A cert row granted under relaxed thresholds carries a '+relaxed'
    marker in granted_by and is not valid for mainnet execution."""
    from matrix_shared.trading_safety import (
        RELAXED_MARKER,
        cert_is_relaxed,
        relaxed_granted_by,
    )

    class _Cert:
        granted_by = "auto-eligibility"

    assert cert_is_relaxed(_Cert()) is False
    _Cert.granted_by = relaxed_granted_by("auto-eligibility")
    assert _Cert.granted_by.endswith(RELAXED_MARKER)
    assert cert_is_relaxed(_Cert()) is True
    # idempotent
    assert relaxed_granted_by(_Cert.granted_by) == _Cert.granted_by

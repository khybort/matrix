"""Unit tests for crypto_universe().

DB-free: covers the env-override path, default fallback, and the
SCREENER_AUTO_INCLUDE flag (currently a no-op placeholder until a sync
screener reader is added to matrix_shared.db).
"""

from __future__ import annotations

import os

import pytest

from matrix_shared.markets.crypto import _DEFAULT_UNIVERSE, crypto_universe


# ---------------------------------------------------------------------------
# Default fallback
# ---------------------------------------------------------------------------
def test_default_universe_returned_when_no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CRYPTO_SYMBOLS", raising=False)
    monkeypatch.delenv("SCREENER_AUTO_INCLUDE", raising=False)
    result = crypto_universe()
    assert result == list(_DEFAULT_UNIVERSE)


def test_default_universe_has_expected_symbols(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CRYPTO_SYMBOLS", raising=False)
    monkeypatch.delenv("SCREENER_AUTO_INCLUDE", raising=False)
    result = crypto_universe()
    assert "BTCUSDT" in result
    assert "ETHUSDT" in result
    assert len(result) >= 15


# ---------------------------------------------------------------------------
# Env override path
# ---------------------------------------------------------------------------
def test_crypto_symbols_env_overrides_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRYPTO_SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT")
    result = crypto_universe()
    assert result == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


def test_crypto_symbols_env_normalises_to_uppercase(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRYPTO_SYMBOLS", "btcusdt, ethusdt , SOLUSDT")
    result = crypto_universe()
    assert result == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


def test_crypto_symbols_env_single_symbol(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRYPTO_SYMBOLS", "BNBUSDT")
    result = crypto_universe()
    assert result == ["BNBUSDT"]


def test_crypto_symbols_env_whitespace_only_falls_through(monkeypatch: pytest.MonkeyPatch) -> None:
    # A blank value is treated as unset — falls back to default.
    monkeypatch.setenv("CRYPTO_SYMBOLS", "   ")
    result = crypto_universe()
    assert result == list(_DEFAULT_UNIVERSE)


# ---------------------------------------------------------------------------
# SCREENER_AUTO_INCLUDE flag
# ---------------------------------------------------------------------------
def test_screener_auto_include_does_not_break_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Flag is parsed without crashing; no-op until sync reader is implemented."""
    monkeypatch.delenv("CRYPTO_SYMBOLS", raising=False)
    monkeypatch.setenv("SCREENER_AUTO_INCLUDE", "true")
    result = crypto_universe()
    # Must return at least the default set (no crash, no empty list).
    assert result == list(_DEFAULT_UNIVERSE)


def test_screener_auto_include_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CRYPTO_SYMBOLS", raising=False)
    for val in ("True", "TRUE", "true"):
        monkeypatch.setenv("SCREENER_AUTO_INCLUDE", val)
        result = crypto_universe()
        assert result == list(_DEFAULT_UNIVERSE)


def test_screener_auto_include_false_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CRYPTO_SYMBOLS", raising=False)
    monkeypatch.setenv("SCREENER_AUTO_INCLUDE", "false")
    result = crypto_universe()
    assert result == list(_DEFAULT_UNIVERSE)


def test_screener_auto_include_ignored_when_crypto_symbols_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CRYPTO_SYMBOLS override wins even when auto-include is on."""
    monkeypatch.setenv("CRYPTO_SYMBOLS", "BTCUSDT")
    monkeypatch.setenv("SCREENER_AUTO_INCLUDE", "true")
    result = crypto_universe()
    assert result == ["BTCUSDT"]

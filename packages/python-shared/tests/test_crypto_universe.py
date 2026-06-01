"""Unit tests for crypto_universe()."""

from __future__ import annotations

import pytest

from matrix_shared.markets.crypto import crypto_universe


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


def test_no_env_no_db_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CRYPTO_SYMBOLS", raising=False)
    result = crypto_universe()
    assert result == []

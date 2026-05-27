"""Pure helpers in agent.main (no DB)."""

from __future__ import annotations

from agent.main import _asset_canonical


def test_crypto_strips_usdt_suffix():
    assert _asset_canonical("BTCUSDT", "crypto") == "BTC"
    assert _asset_canonical("ETHUSDC", "crypto") == "ETH"


def test_crypto_without_known_quote_is_unchanged():
    assert _asset_canonical("WEIRD", "crypto") == "WEIRD"


def test_bist_symbol_is_unchanged():
    assert _asset_canonical("THYAO", "bist") == "THYAO"

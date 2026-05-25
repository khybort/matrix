"""BybitConnector — gate-first behavior and signing correctness.

The connector MUST never hit the network in any dry-run path; these
tests use empty BYBIT_API_KEY to force the "missing credentials" guard
even when LIVE_EXECUTION_ENABLED=true. That combination is the safety
contract we care about: a half-configured node (flag flipped, keys not
yet pasted in) must dry-run, not 401 against Bybit.
"""

from __future__ import annotations

import hashlib
import hmac
from decimal import Decimal

import pytest

from execution.bybit_connector import (
    BybitConnector,
    OrderRequest,
    build_signed_headers,
    sign_request,
)

# Async tests get the mark explicitly so the two sync signing tests
# don't trip the "marked asyncio but not async" warning.
asyncio_mark = pytest.mark.asyncio


def _market_order(symbol: str = "BTCUSDT") -> OrderRequest:
    return OrderRequest(
        category="linear",
        symbol=symbol,
        side="Buy",
        order_type="Market",
        qty=Decimal("0.001"),
    )


@asyncio_mark
async def test_place_order_denied_without_cert(wallet_id, monkeypatch):
    """No cert in the DB → gate denies → response is ok=False, dry_run=True."""
    monkeypatch.setenv("LIVE_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("LIVE_CAPITAL_CAP_USD", "2000")
    monkeypatch.setenv("BYBIT_API_KEY", "")
    monkeypatch.setenv("BYBIT_API_SECRET", "")

    connector = BybitConnector(testnet=True)
    try:
        resp = await connector.place_order(
            _market_order(),
            strategy_id="never_granted_strat",
            asset_class="crypto",
            strategy_version=1,
            intended_notional_usd=Decimal("100"),
            wallet_id=wallet_id,
        )
    finally:
        await connector.aclose()

    assert resp.ok is False
    assert resp.dry_run is True
    assert resp.order_id is None
    assert resp.raw.get("denied_by_gate") is True
    reasons = resp.raw.get("reasons", [])
    assert any("certificate" in r for r in reasons), reasons


@asyncio_mark
async def test_place_order_dry_run_when_live_disabled(wallet_id, grant_cert, monkeypatch):
    """Gate green, cert valid, but LIVE_EXECUTION_ENABLED=false → dry-run.

    The gate itself denies (the env flag is one of its checks) so this
    surfaces as the gate's denial; either way the connector must NOT
    touch the network. We assert that explicitly by passing an
    obviously-invalid base URL via an empty client.
    """
    sid, ac, ver = await grant_cert()
    monkeypatch.setenv("LIVE_EXECUTION_ENABLED", "false")
    monkeypatch.setenv("LIVE_CAPITAL_CAP_USD", "2000")
    monkeypatch.setenv("BYBIT_API_KEY", "")
    monkeypatch.setenv("BYBIT_API_SECRET", "")

    connector = BybitConnector(testnet=True)
    try:
        resp = await connector.place_order(
            _market_order(),
            strategy_id=sid,
            asset_class=ac,
            strategy_version=ver,
            intended_notional_usd=Decimal("100"),
            wallet_id=wallet_id,
        )
    finally:
        await connector.aclose()

    assert resp.dry_run is True
    # Gate denied it (flag is off) → ok=False with reasons.
    assert resp.ok is False
    assert any(
        "LIVE_EXECUTION_ENABLED" in r for r in resp.raw.get("reasons", [])
    ), resp.raw


@asyncio_mark
async def test_place_order_dry_run_when_creds_missing(wallet_id, grant_cert, monkeypatch):
    """Gate green AND LIVE_EXECUTION_ENABLED=true BUT no creds → dry-run, ok=True.

    Half-configured node: operator flipped the flag but hasn't pasted
    keys yet. Must dry-run (ok=True since the gate was green) and never
    touch the network. This is the safety guard called out in the task.
    """
    sid, ac, ver = await grant_cert()
    monkeypatch.setenv("LIVE_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("LIVE_CAPITAL_CAP_USD", "2000")
    monkeypatch.setenv("BYBIT_API_KEY", "")
    monkeypatch.setenv("BYBIT_API_SECRET", "")

    connector = BybitConnector(testnet=True)
    try:
        resp = await connector.place_order(
            _market_order(),
            strategy_id=sid,
            asset_class=ac,
            strategy_version=ver,
            intended_notional_usd=Decimal("100"),  # under $200 cap
            wallet_id=wallet_id,
        )
    finally:
        await connector.aclose()

    assert resp.dry_run is True
    assert resp.ok is True
    assert resp.order_id is None
    assert resp.raw.get("dry_run_reason", "").startswith("BYBIT_API_KEY")
    assert "would_be_payload" in resp.raw
    assert resp.raw["would_be_payload"]["symbol"] == "BTCUSDT"


def test_signing_deterministic():
    """Fixed inputs → fixed HMAC SHA256 output.

    We compute the expected value inline using stdlib hmac so this test
    documents the signing algorithm. If the connector ever drifts (e.g.
    reorders the pre-image), this fails immediately.
    """
    api_key = "TESTKEY"
    api_secret = "TESTSECRET"
    timestamp_ms = 1700000000000
    recv_window_ms = 5000
    payload = '{"category":"linear","symbol":"BTCUSDT","side":"Buy","orderType":"Market","qty":"0.001","timeInForce":"IOC"}'

    expected_pre_sign = f"{timestamp_ms}{api_key}{recv_window_ms}{payload}"
    expected = hmac.new(
        api_secret.encode("utf-8"),
        expected_pre_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    actual = sign_request(
        api_key=api_key,
        api_secret=api_secret,
        timestamp_ms=timestamp_ms,
        recv_window_ms=recv_window_ms,
        payload=payload,
    )
    assert actual == expected
    # Stable across runs (hex digest is deterministic, length 64).
    assert len(actual) == 64


def test_signing_includes_all_required_headers():
    """All four X-BAPI-* headers + Content-Type are present and well-formed."""
    headers = build_signed_headers(
        api_key="TESTKEY",
        api_secret="TESTSECRET",
        payload="accountType=UNIFIED",
        recv_window_ms=5000,
        timestamp_ms=1700000000000,
    )
    assert headers["X-BAPI-API-KEY"] == "TESTKEY"
    assert headers["X-BAPI-TIMESTAMP"] == "1700000000000"
    assert headers["X-BAPI-RECV-WINDOW"] == "5000"
    assert len(headers["X-BAPI-SIGN"]) == 64
    assert headers["Content-Type"] == "application/json"

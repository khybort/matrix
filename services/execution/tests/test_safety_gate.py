"""execution.safety.should_submit_live — gate must deny in every bad state.

Each test isolates one failure mode so a regression in any single layer
fails loudly rather than passing silently because another layer happens
to catch the bad call. The happy path is also covered, so we know the
gate doesn't deny everything indiscriminately.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import delete, update

from matrix_shared import shared_session_scope
from matrix_shared.models import PaperPosition, Wallet

from execution.safety import should_submit_live

pytestmark = pytest.mark.asyncio


# --- failure-mode tests -------------------------------------------------

async def test_denies_when_no_certificate(wallet_id, monkeypatch):
    """No cert in the DB at all → gate must deny."""
    monkeypatch.setenv("LIVE_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("LIVE_CAPITAL_CAP_USD", "2000")
    decision = await should_submit_live(
        strategy_id="never_granted_strat",
        asset_class="crypto",
        strategy_version=1,
        intended_notional_usd=Decimal("100"),
        wallet_id=wallet_id,
    )
    assert decision.allowed is False
    assert any("certificate" in r for r in decision.reasons), decision.reasons


async def test_denies_when_live_execution_flag_is_false(wallet_id, grant_cert, monkeypatch):
    """Cert exists, wallet healthy, but env flag off → gate denies."""
    sid, ac, ver = await grant_cert()
    monkeypatch.setenv("LIVE_EXECUTION_ENABLED", "false")
    monkeypatch.setenv("LIVE_CAPITAL_CAP_USD", "2000")
    decision = await should_submit_live(
        strategy_id=sid, asset_class=ac, strategy_version=ver,
        intended_notional_usd=Decimal("100"),
        wallet_id=wallet_id,
    )
    assert decision.allowed is False
    assert any("LIVE_EXECUTION_ENABLED" in r for r in decision.reasons), decision.reasons


async def test_denies_when_circuit_tripped(wallet_id, grant_cert, monkeypatch):
    """Daily-loss circuit breaker overrides everything else."""
    sid, ac, ver = await grant_cert()
    monkeypatch.setenv("LIVE_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("LIVE_CAPITAL_CAP_USD", "2000")
    async with shared_session_scope() as session:
        await session.execute(
            update(Wallet)
            .where(Wallet.id == wallet_id)
            .values(circuit_tripped_at=datetime.now(timezone.utc))
        )
    decision = await should_submit_live(
        strategy_id=sid, asset_class=ac, strategy_version=ver,
        intended_notional_usd=Decimal("100"),
        wallet_id=wallet_id,
    )
    assert decision.allowed is False
    assert any("circuit" in r for r in decision.reasons), decision.reasons


async def test_denies_when_notional_exceeds_position_cap(wallet_id, grant_cert, monkeypatch):
    """Default max_position_pct=0.02 of $10k = $200 cap; ask for $500."""
    sid, ac, ver = await grant_cert()
    monkeypatch.setenv("LIVE_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("LIVE_CAPITAL_CAP_USD", "2000")
    decision = await should_submit_live(
        strategy_id=sid, asset_class=ac, strategy_version=ver,
        intended_notional_usd=Decimal("500"),
        wallet_id=wallet_id,
    )
    assert decision.allowed is False
    assert any("max_position_pct" in r for r in decision.reasons), decision.reasons


async def test_denies_when_concurrent_positions_at_cap(wallet_id, grant_cert, monkeypatch):
    """5 already-open positions → gate refuses a 6th."""
    sid, ac, ver = await grant_cert()
    monkeypatch.setenv("LIVE_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("LIVE_CAPITAL_CAP_USD", "2000")

    # Need predictions to satisfy paper_positions.prediction_id FK constraint.
    # Two-phase: commit Predictions first so FK targets exist, then Positions.
    # (One session works in theory but the topo-sort wasn't reliable here.)
    from matrix_shared.models import Prediction
    pred_ids: list[uuid.UUID] = []
    async with shared_session_scope() as session:
        for i in range(5):
            pid = uuid.uuid4()
            pred_ids.append(pid)
            session.add(
                Prediction(
                    id=pid, strategy_id=sid, strategy_version=ver, asset_class=ac,
                    symbol="BTCUSDT", exchange="bybit", side="long",
                    confidence=Decimal("0.5"), horizon_seconds=60,
                    generated_at=datetime.now(timezone.utc),
                    close_by=datetime.now(timezone.utc),
                    entry_price_ref=Decimal("100"),
                    status="open",
                )
            )
    async with shared_session_scope() as session:
        for pid in pred_ids:
            session.add(
                PaperPosition(
                    id=uuid.uuid4(), prediction_id=pid, symbol="BTCUSDT",
                    exchange="bybit", side="long", notional_usd=Decimal("50"),
                    opened_at=datetime.now(timezone.utc),
                    opened_price=Decimal("100"),
                    status="open", wallet_id=wallet_id, asset_class=ac,
                )
            )

    try:
        decision = await should_submit_live(
            strategy_id=sid, asset_class=ac, strategy_version=ver,
            intended_notional_usd=Decimal("100"),
            wallet_id=wallet_id,
        )
        assert decision.allowed is False
        assert any("positions open" in r for r in decision.reasons), decision.reasons
    finally:
        # Clean up the 5 positions + their predictions
        async with shared_session_scope() as session:
            await session.execute(delete(PaperPosition).where(PaperPosition.wallet_id == wallet_id))
            await session.execute(delete(Prediction).where(Prediction.strategy_id == sid))


async def test_denies_when_capital_cap_exceeded(wallet_id, grant_cert, monkeypatch):
    """LIVE_CAPITAL_CAP_USD=100 ceiling, asking for $150."""
    sid, ac, ver = await grant_cert()
    monkeypatch.setenv("LIVE_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("LIVE_CAPITAL_CAP_USD", "100")
    decision = await should_submit_live(
        strategy_id=sid, asset_class=ac, strategy_version=ver,
        intended_notional_usd=Decimal("150"),
        wallet_id=wallet_id,
    )
    assert decision.allowed is False
    assert any("LIVE_CAPITAL_CAP_USD" in r for r in decision.reasons), decision.reasons


async def test_denies_when_certificate_expired(wallet_id, grant_cert, monkeypatch):
    """Cert with validity_until already in the past → gate treats as missing."""
    sid, ac, ver = await grant_cert(validity_hours=-1)  # expired
    monkeypatch.setenv("LIVE_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("LIVE_CAPITAL_CAP_USD", "2000")
    decision = await should_submit_live(
        strategy_id=sid, asset_class=ac, strategy_version=ver,
        intended_notional_usd=Decimal("50"),
        wallet_id=wallet_id,
    )
    assert decision.allowed is False
    assert any("certificate" in r for r in decision.reasons), decision.reasons


# --- happy path ---------------------------------------------------------

async def test_allows_when_every_gate_is_green(wallet_id, grant_cert, monkeypatch):
    """All checks pass → allowed=True, no reasons."""
    sid, ac, ver = await grant_cert(validity_hours=24)
    monkeypatch.setenv("LIVE_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("LIVE_CAPITAL_CAP_USD", "2000")
    decision = await should_submit_live(
        strategy_id=sid, asset_class=ac, strategy_version=ver,
        intended_notional_usd=Decimal("100"),  # well under $200 (2% of $10k)
        wallet_id=wallet_id,
    )
    assert decision.allowed is True, decision.reasons
    assert decision.reasons == []


# --- mainnet + relaxed cert thresholds -----------------------------------

async def test_denies_on_mainnet_when_cert_overrides_are_set(wallet_id, grant_cert, monkeypatch):
    """BYBIT_TESTNET=false + any MATRIX_CERT_* override → hard deny, even
    with a valid cert and every other gate green (docs/TRADING.md: cert
    thresholds cannot be softened for live capital)."""
    sid, ac, ver = await grant_cert(validity_hours=24)
    monkeypatch.setenv("LIVE_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("LIVE_CAPITAL_CAP_USD", "2000")
    monkeypatch.setenv("BYBIT_TESTNET", "false")
    monkeypatch.setenv("MATRIX_CERT_MIN_OUTCOMES", "20")
    decision = await should_submit_live(
        strategy_id=sid, asset_class=ac, strategy_version=ver,
        intended_notional_usd=Decimal("100"),
        wallet_id=wallet_id,
    )
    assert decision.allowed is False
    assert any("MATRIX_CERT_MIN_OUTCOMES" in r for r in decision.reasons), decision.reasons


async def test_relaxed_cert_rejected_on_mainnet_but_ok_on_testnet(wallet_id, grant_cert, monkeypatch):
    """Cert granted under relaxed thresholds (granted_by '+relaxed') is fine
    for testnet shadowing but invalid once the venue is mainnet."""
    from matrix_shared.trading_safety import relaxed_granted_by
    sid, ac, ver = await grant_cert(validity_hours=24)
    async with shared_session_scope() as session:
        from matrix_shared.models import PaperTradeCertificate
        await session.execute(
            update(PaperTradeCertificate)
            .where(PaperTradeCertificate.strategy_id == sid)
            .values(granted_by=relaxed_granted_by("auto-eligibility"))
        )
    for key in ("MATRIX_CERT_MIN_OBSERVATION_DAYS", "MATRIX_CERT_MIN_OUTCOMES",
                "MATRIX_CERT_MIN_WIN_RATE", "MATRIX_CERT_MIN_TOTAL_PNL_USD",
                "MATRIX_CERT_MAX_DRAWDOWN_PCT"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("LIVE_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("LIVE_CAPITAL_CAP_USD", "2000")

    monkeypatch.setenv("BYBIT_TESTNET", "true")
    ok = await should_submit_live(
        strategy_id=sid, asset_class=ac, strategy_version=ver,
        intended_notional_usd=Decimal("100"), wallet_id=wallet_id,
    )
    assert ok.allowed is True, ok.reasons

    monkeypatch.setenv("BYBIT_TESTNET", "false")
    denied = await should_submit_live(
        strategy_id=sid, asset_class=ac, strategy_version=ver,
        intended_notional_usd=Decimal("100"), wallet_id=wallet_id,
    )
    assert denied.allowed is False
    assert any("certificate" in r for r in denied.reasons), denied.reasons


async def test_legacy_cert_with_weak_evidence_rejected_on_mainnet(wallet_id, grant_cert, monkeypatch):
    """Cert granted before the relaxed marker existed but whose evidence
    snapshot is below TRADING.md defaults must not unlock mainnet."""
    sid, ac, ver = await grant_cert(validity_hours=24)
    async with shared_session_scope() as session:
        from matrix_shared.models import PaperTradeCertificate
        await session.execute(
            update(PaperTradeCertificate)
            .where(PaperTradeCertificate.strategy_id == sid)
            .values(observation_days=3, n_outcomes=45, total_pnl_usd=Decimal("-5"))
        )
    for key in ("MATRIX_CERT_MIN_OBSERVATION_DAYS", "MATRIX_CERT_MIN_OUTCOMES",
                "MATRIX_CERT_MIN_WIN_RATE", "MATRIX_CERT_MIN_TOTAL_PNL_USD",
                "MATRIX_CERT_MAX_DRAWDOWN_PCT"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("LIVE_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("LIVE_CAPITAL_CAP_USD", "2000")
    monkeypatch.setenv("BYBIT_TESTNET", "true")
    ok = await should_submit_live(
        strategy_id=sid, asset_class=ac, strategy_version=ver,
        intended_notional_usd=Decimal("100"), wallet_id=wallet_id,
    )
    assert ok.allowed is True, ok.reasons
    monkeypatch.setenv("BYBIT_TESTNET", "false")
    denied = await should_submit_live(
        strategy_id=sid, asset_class=ac, strategy_version=ver,
        intended_notional_usd=Decimal("100"), wallet_id=wallet_id,
    )
    assert denied.allowed is False
    assert any("certificate" in r for r in denied.reasons), denied.reasons


async def test_closing_bypasses_caps_but_not_flag_or_cert(wallet_id, grant_cert, monkeypatch):
    """Reduce-only exits must never be capped (stranded exposure), but still
    obey posture/flag/cert."""
    sid, ac, ver = await grant_cert(validity_hours=24)
    monkeypatch.setenv("LIVE_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("LIVE_CAPITAL_CAP_USD", "1")  # would fail an open
    monkeypatch.setenv("BYBIT_TESTNET", "true")
    for key in ("MATRIX_CERT_MIN_OBSERVATION_DAYS", "MATRIX_CERT_MIN_OUTCOMES",
                "MATRIX_CERT_MIN_WIN_RATE", "MATRIX_CERT_MIN_TOTAL_PNL_USD",
                "MATRIX_CERT_MAX_DRAWDOWN_PCT"):
        monkeypatch.delenv(key, raising=False)
    opening = await should_submit_live(
        strategy_id=sid, asset_class=ac, strategy_version=ver,
        intended_notional_usd=Decimal("5000"), wallet_id=wallet_id,
    )
    assert opening.allowed is False
    closing = await should_submit_live(
        strategy_id=sid, asset_class=ac, strategy_version=ver,
        intended_notional_usd=Decimal("5000"), wallet_id=wallet_id, closing=True,
    )
    assert closing.allowed is True, closing.reasons
    monkeypatch.setenv("LIVE_EXECUTION_ENABLED", "false")
    denied = await should_submit_live(
        strategy_id=sid, asset_class=ac, strategy_version=ver,
        intended_notional_usd=Decimal("5000"), wallet_id=wallet_id, closing=True,
    )
    assert denied.allowed is False

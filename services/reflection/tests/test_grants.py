"""Auto-grant cert pipeline (matrix_shared.trading_safety.maybe_grant_certificate).

Tests run against matrix-postgres-shared (port 5433). Eligibility kwargs are
loosened in the tests so 10 outcomes / 0 observation days is enough — full
defaults from docs/TRADING.md would need 60 days of paper-trade history.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from matrix_shared import maybe_grant_certificate, shared_session_scope
from matrix_shared.models import PaperTradeCertificate

pytestmark = pytest.mark.asyncio


# Loosen thresholds so the test fixtures (10 outcomes, same day) qualify.
_LOOSE = dict(
    min_observation_days=0,
    min_outcomes=5,
    min_win_rate=Decimal("0.0"),
    min_total_pnl_usd=Decimal("-100"),
    max_drawdown_pct=Decimal("1.0"),
    min_ci_lower_usd=Decimal("-100"),
)


async def _fetch(strategy_id: str, asset_class: str, version: int) -> PaperTradeCertificate | None:
    async with shared_session_scope() as session:
        stmt = (
            select(PaperTradeCertificate)
            .where(PaperTradeCertificate.strategy_id == strategy_id)
            .where(PaperTradeCertificate.asset_class == asset_class)
            .where(PaperTradeCertificate.version == version)
        )
        return (await session.execute(stmt)).scalar_one_or_none()


async def test_grants_when_eligible(seed_outcomes, cert_cleanup):
    sid = f"grant_a_{uuid.uuid4().hex[:6]}"
    ac, ver = "crypto", 1
    cert_cleanup.append((sid, ac, ver))
    await seed_outcomes(sid, ac, ver, n=10, win_rate=0.6)

    ok, verdict, reason = await maybe_grant_certificate(sid, ac, ver, **_LOOSE)
    assert ok is True, (reason, verdict)
    row = await _fetch(sid, ac, ver)
    assert row is not None
    assert row.status == "granted"
    assert row.validity_until is not None
    assert row.validity_until > datetime.now(timezone.utc)
    assert row.granted_by.startswith("auto-eligibility")  # "+relaxed" suffix depends on MATRIX_CERT_* env
    assert row.n_outcomes == 10


async def test_does_not_double_grant(seed_outcomes, cert_cleanup):
    sid = f"grant_b_{uuid.uuid4().hex[:6]}"
    ac, ver = "crypto", 1
    cert_cleanup.append((sid, ac, ver))
    await seed_outcomes(sid, ac, ver, n=10, win_rate=0.6)

    ok1, _, _ = await maybe_grant_certificate(sid, ac, ver, **_LOOSE)
    assert ok1 is True
    ok2, verdict2, reason2 = await maybe_grant_certificate(sid, ac, ver, **_LOOSE)
    assert ok2 is False
    assert reason2 == "already granted"
    assert verdict2 is None


async def test_upgrades_pending_to_granted(seed_outcomes, cert_cleanup):
    sid = f"grant_c_{uuid.uuid4().hex[:6]}"
    ac, ver = "crypto", 1
    cert_cleanup.append((sid, ac, ver))
    await seed_outcomes(sid, ac, ver, n=10, win_rate=0.6)
    # Pre-seed a 'pending' cert
    async with shared_session_scope() as session:
        session.add(PaperTradeCertificate(
            strategy_id=sid, asset_class=ac, version=ver, status="pending",
        ))

    ok, _, _ = await maybe_grant_certificate(sid, ac, ver, **_LOOSE)
    assert ok is True
    row = await _fetch(sid, ac, ver)
    assert row.status == "granted"


async def test_regrants_after_expiry(seed_outcomes, cert_cleanup):
    sid = f"grant_d_{uuid.uuid4().hex[:6]}"
    ac, ver = "crypto", 1
    cert_cleanup.append((sid, ac, ver))
    await seed_outcomes(sid, ac, ver, n=10, win_rate=0.6)
    # Pre-seed an EXPIRED granted cert (validity_until in the past)
    async with shared_session_scope() as session:
        session.add(PaperTradeCertificate(
            strategy_id=sid, asset_class=ac, version=ver,
            status="granted",
            granted_at=datetime.now(timezone.utc) - timedelta(days=30),
            validity_until=datetime.now(timezone.utc) - timedelta(days=1),
        ))

    ok, _, _ = await maybe_grant_certificate(sid, ac, ver, **_LOOSE)
    assert ok is True
    row = await _fetch(sid, ac, ver)
    assert row.status == "granted"
    assert row.validity_until > datetime.now(timezone.utc)


async def test_skips_when_not_eligible(seed_outcomes, cert_cleanup):
    sid = f"grant_e_{uuid.uuid4().hex[:6]}"
    ac, ver = "crypto", 1
    cert_cleanup.append((sid, ac, ver))
    await seed_outcomes(sid, ac, ver, n=3, win_rate=0.6)  # n < min_outcomes=5

    ok, verdict, reason = await maybe_grant_certificate(sid, ac, ver, **_LOOSE)
    assert ok is False
    assert reason == "not eligible"
    assert verdict is not None
    assert "n_outcomes" in " ".join(verdict.reasons)
    row = await _fetch(sid, ac, ver)
    assert row is None


async def test_ci_lower_bound_blocks_lucky_positive_totals(seed_outcomes, cert_cleanup):
    """Positive total but wide dispersion → CI lower bound < 0 → not eligible."""
    from matrix_shared import evaluate_eligibility
    sid = f"grant_ci_{uuid.uuid4().hex[:6]}"
    cert_cleanup.append((sid, "crypto", 1))
    await seed_outcomes(sid, "crypto", 1, n=10, win_rate=0.6, pnl_per_trade_usd=1.0)  # +6/-4 → mean 0.2, se≈0.33
    v = await evaluate_eligibility(sid, "crypto", 1, min_observation_days=0, min_outcomes=5,
                                   min_win_rate=Decimal("0"), max_drawdown_pct=Decimal("1"),
                                   min_total_pnl_usd=Decimal("0"))
    assert v.eligible is False
    assert any(r.startswith("ci_lower_usd") for r in v.reasons), v.reasons
    assert Decimal(v.metrics["ci_lower_usd"]) < 0


async def test_revoke_breached_certificates(seed_outcomes, cert_cleanup):
    from matrix_shared import maybe_grant_certificate
    from matrix_shared.trading_safety import revoke_breached_certificates
    sid = f"grant_rv_{uuid.uuid4().hex[:6]}"
    cert_cleanup.append((sid, "crypto", 1))
    await seed_outcomes(sid, "crypto", 1, n=10, win_rate=0.6)
    ok, _, _ = await maybe_grant_certificate(sid, "crypto", 1, **_LOOSE)
    assert ok
    # a crash: 20 losing trades of $300 → drawdown ≈ 60% of the $10k reference
    await seed_outcomes(sid, "crypto", 1, n=20, win_rate=0.0, pnl_per_trade_usd=300.0)
    revoked = await revoke_breached_certificates(max_drawdown_pct=Decimal("0.15"))
    assert f"{sid}/crypto/v1" in revoked
    row = await _fetch(sid, "crypto", 1)
    assert row.status == "revoked" and row.revoked_reason.startswith("auto:")


async def test_re_emitted_outcomes_count_as_one_bet(seed_outcomes, cert_cleanup):
    """Ten fills of the same (symbol, side) whose signals re-emitted inside the
    first one's 60 s horizon are one bet: they must not satisfy `min_outcomes`
    ten times over. The dollars still all count."""
    from matrix_shared.trading_safety import evaluate_eligibility

    sid = f"grant_dup_{uuid.uuid4().hex[:6]}"
    ac, ver = "crypto", 1
    cert_cleanup.append((sid, ac, ver))
    await seed_outcomes(sid, ac, ver, n=10, win_rate=1.0, spacing_s=5)

    v = await evaluate_eligibility(sid, ac, ver, **_LOOSE)
    assert v.metrics["n_outcomes_raw"] == 10
    assert v.metrics["n_outcomes"] == 1
    assert Decimal(v.metrics["total_pnl_usd"]) == Decimal("10")
    assert not v.eligible and any(r.startswith("n_outcomes=1 ") for r in v.reasons)

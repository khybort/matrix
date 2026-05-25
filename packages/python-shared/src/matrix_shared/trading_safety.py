"""Trading-safety primitives — code-enforced ceilings the rest of the system
can NEITHER mutate at runtime NOR bypass via env vars.

This module is intentionally tiny. Add only what HAS to live at code level;
configuration belongs in `config.py`.

Anything here mirrors a written promise in docs/TRADING.md. If you find
yourself wanting to soften a check, the answer is no — open the doc,
re-read the relevant section, and propose a change to BOTH the doc and the
code in the same commit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from matrix_shared.db import shared_session_scope
from matrix_shared.models import Outcome, PaperTradeCertificate, Prediction

# Hardcoded. Do NOT load this from env. Do NOT add an override mechanism.
# Live execution must always check has_valid_certificate() before submitting
# any real-money order; turning this off requires editing this file and
# shipping a commit. See docs/TRADING.md for the rationale.
LIVE_EXECUTION_REQUIRES_CERT: bool = True


async def has_valid_certificate(
    strategy_id: str,
    asset_class: str,
    version: int,
) -> bool:
    """SAFETY GATE — execution layer must consult this before live order submit.

    Returns True iff a 'granted' certificate exists for the given
    (strategy_id, asset_class, version) AND either has no validity expiry or
    its validity_until is in the future. Any other state — pending, revoked,
    expired, missing — returns False.
    """
    async with shared_session_scope() as session:
        stmt = (
            select(PaperTradeCertificate)
            .where(PaperTradeCertificate.strategy_id == strategy_id)
            .where(PaperTradeCertificate.asset_class == asset_class)
            .where(PaperTradeCertificate.version == version)
            .limit(1)
        )
        cert = (await session.execute(stmt)).scalar_one_or_none()

    if cert is None:
        return False
    if cert.status != "granted":
        return False
    if cert.validity_until is not None and cert.validity_until < datetime.now(timezone.utc):
        return False
    return True


# Eligibility thresholds — defaults reflect docs/TRADING.md. Loosen only by
# explicit kwargs (e.g. for dry-run inspection), never via env.
DEFAULT_MIN_OBSERVATION_DAYS = 60
DEFAULT_MIN_OUTCOMES = 200
DEFAULT_MIN_WIN_RATE = Decimal("0.40")
DEFAULT_MIN_TOTAL_PNL_USD = Decimal("0.00")
DEFAULT_MAX_DRAWDOWN_PCT = Decimal("0.15")

# Drawdown denominator. Cumulative-PnL peak alone breaks down in early Phase
# when peak is near zero; expressing dd as a fraction of starting capital
# gives a stable, equity-anchored number (matches docs/TRADING.md's
# "drawdown < 15%" intent: 15% of risk capital, not 15% of running peak).
_DRAWDOWN_REFERENCE_USD = Decimal("10000")  # default wallet starting capital


@dataclass(slots=True)
class EligibilityVerdict:
    eligible: bool
    reasons: list[str]
    metrics: dict[str, object]


async def evaluate_eligibility(
    strategy_id: str,
    asset_class: str,
    version: int,
    *,
    min_observation_days: int = DEFAULT_MIN_OBSERVATION_DAYS,
    min_outcomes: int = DEFAULT_MIN_OUTCOMES,
    min_win_rate: Decimal = DEFAULT_MIN_WIN_RATE,
    min_total_pnl_usd: Decimal = DEFAULT_MIN_TOTAL_PNL_USD,
    max_drawdown_pct: Decimal = DEFAULT_MAX_DRAWDOWN_PCT,
) -> EligibilityVerdict:
    """Compute paper-trade metrics for a strategy/version and return verdict.

    NOTE: This is the analysis step, not the grant. Granting writes a row
    via a separate code path (auto-grant or manual review). This function
    is what that grant path consults.

    Drawdown is approximated as max cumulative loss from a running peak,
    using outcome.pnl_usd ordered by observed_at. Good enough for the
    early-Phase paper data; can be tightened later.
    """
    async with shared_session_scope() as session:
        stmt = (
            select(
                Outcome.pnl_usd,
                Outcome.observed_at,
            )
            .join(Prediction, Prediction.id == Outcome.prediction_id)
            .where(Prediction.strategy_id == strategy_id)
            .where(Prediction.asset_class == asset_class)
            .where(Prediction.strategy_version == version)
            .order_by(Outcome.observed_at.asc())
        )
        rows = list((await session.execute(stmt)).all())

    n = len(rows)
    if n == 0:
        return EligibilityVerdict(
            eligible=False,
            reasons=["no outcomes recorded"],
            metrics={"n_outcomes": 0, "observation_days": 0},
        )

    first_ts = rows[0].observed_at
    last_ts = rows[-1].observed_at
    observation_days = max(0, (last_ts - first_ts).days)

    wins = sum(1 for r in rows if r.pnl_usd > 0)
    win_rate = Decimal(wins) / Decimal(n)
    total_pnl = sum((Decimal(r.pnl_usd) for r in rows), Decimal("0"))
    avg_pnl = total_pnl / Decimal(n)

    # Drawdown: cumulative peak-to-trough on equity (starting capital +
    # running PnL). Anchoring to starting capital avoids the "tiny peak →
    # huge percentage" trap when running PnL hovers near zero.
    equity = _DRAWDOWN_REFERENCE_USD
    peak = equity
    worst_dd = Decimal("0")
    for r in rows:
        equity += Decimal(r.pnl_usd)
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > worst_dd:
            worst_dd = dd
    dd_pct = worst_dd / _DRAWDOWN_REFERENCE_USD

    reasons: list[str] = []
    if observation_days < min_observation_days:
        reasons.append(f"observation_days={observation_days} < {min_observation_days}")
    if n < min_outcomes:
        reasons.append(f"n_outcomes={n} < {min_outcomes}")
    if win_rate < min_win_rate:
        reasons.append(f"win_rate={win_rate:.4f} < {min_win_rate}")
    if total_pnl < min_total_pnl_usd:
        reasons.append(f"total_pnl_usd={total_pnl:.4f} < {min_total_pnl_usd}")
    if dd_pct > max_drawdown_pct:
        reasons.append(f"max_drawdown_pct={dd_pct:.4f} > {max_drawdown_pct}")

    return EligibilityVerdict(
        eligible=len(reasons) == 0,
        reasons=reasons,
        metrics={
            "n_outcomes": n,
            "observation_days": observation_days,
            "win_rate": str(win_rate.quantize(Decimal("0.000001"))),
            "avg_pnl_usd": str(avg_pnl.quantize(Decimal("0.000001"))),
            "total_pnl_usd": str(total_pnl.quantize(Decimal("0.000001"))),
            "max_drawdown_pct": str(dd_pct.quantize(Decimal("0.000001"))),
        },
    )

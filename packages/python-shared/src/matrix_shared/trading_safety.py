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

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from loguru import logger
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
    if is_mainnet() and not cert_meets_production_thresholds(cert):
        # Granted while MATRIX_CERT_* overrides were active, or its evidence
        # snapshot is below docs/TRADING.md defaults — good enough for testnet
        # shadowing, never for real capital (gate #5).
        return False
    return True


# Eligibility thresholds — defaults reflect docs/TRADING.md. Loosen only by
# explicit kwargs (e.g. for dry-run inspection), never via env.
DEFAULT_MIN_OBSERVATION_DAYS = 60
DEFAULT_MIN_OUTCOMES = 200
DEFAULT_MIN_WIN_RATE = Decimal("0.40")
DEFAULT_MIN_TOTAL_PNL_USD = Decimal("0.00")
DEFAULT_MAX_DRAWDOWN_PCT = Decimal("0.15")
# Lower bound of the 95% CI on mean pnl/outcome must clear this (docs/AUTONOMY_PLAN.md
# P1.6): a positive total built on a few lucky trades is not "positive expected value".
DEFAULT_MIN_CI_LOWER_USD = Decimal("0.00")

# Optional .env overrides for cert grants (defaults above = docs/TRADING.md).
# They exist so a testnet/shadow deployment can exercise the live-order code
# path early. They are IGNORED on mainnet (see is_mainnet), and any cert that
# was granted while they were active is stamped RELAXED_MARKER and refused by
# has_valid_certificate on mainnet. Unset the vars to restore production
# thresholds; there is no env that turns the mainnet refusal off.
_ENV_MIN_OBSERVATION_DAYS = "MATRIX_CERT_MIN_OBSERVATION_DAYS"
_ENV_MIN_OUTCOMES = "MATRIX_CERT_MIN_OUTCOMES"
_ENV_MIN_WIN_RATE = "MATRIX_CERT_MIN_WIN_RATE"
_ENV_MIN_TOTAL_PNL_USD = "MATRIX_CERT_MIN_TOTAL_PNL_USD"
_ENV_MAX_DRAWDOWN_PCT = "MATRIX_CERT_MAX_DRAWDOWN_PCT"
_ENV_MIN_CI_LOWER_USD = "MATRIX_CERT_MIN_CI_LOWER_USD"
CERT_OVERRIDE_ENV_KEYS: tuple[str, ...] = (
    _ENV_MIN_OBSERVATION_DAYS,
    _ENV_MIN_OUTCOMES,
    _ENV_MIN_WIN_RATE,
    _ENV_MIN_TOTAL_PNL_USD,
    _ENV_MAX_DRAWDOWN_PCT,
    _ENV_MIN_CI_LOWER_USD,
)

# granted_by suffix for certs issued under relaxed thresholds.
RELAXED_MARKER = "+relaxed"


def is_mainnet() -> bool:
    """True when ANY wired venue points at real money.

    Bybit: BYBIT_TESTNET defaults to true; only the literal 'false' is mainnet.
    Alpaca: ALPACA_PAPER defaults to true; only the literal 'false' is live.
    """
    bybit_live = os.environ.get("BYBIT_TESTNET", "true").strip().lower() == "false"
    alpaca_live = os.environ.get("ALPACA_PAPER", "true").strip().lower() == "false"
    return bybit_live or alpaca_live


def cert_overrides_active() -> list[str]:
    """Names of MATRIX_CERT_* env vars currently set to a non-empty value."""
    return [k for k in CERT_OVERRIDE_ENV_KEYS if os.environ.get(k, "").strip()]


def mainnet_refusal_reasons() -> list[str]:
    """Reasons a live order must be refused purely on deployment posture.

    Empty on testnet/paper. On mainnet, any active MATRIX_CERT_* override is
    a hard refusal — the certificate a strategy holds may have been granted
    against softened thresholds, and TRADING.md forbids that for capital.
    """
    if not is_mainnet():
        return []
    active = cert_overrides_active()
    if not active:
        return []
    return [
        "mainnet venue configured while cert thresholds are overridden: "
        + ", ".join(active)
        + " — unset them (docs/TRADING.md forbids relaxed certs for live capital)"
    ]


def relaxed_granted_by(granted_by: str) -> str:
    return granted_by if granted_by.endswith(RELAXED_MARKER) else granted_by + RELAXED_MARKER


def cert_is_relaxed(cert) -> bool:
    gb = getattr(cert, "granted_by", None) or ""
    return gb.endswith(RELAXED_MARKER)


def cert_meets_production_thresholds(cert) -> bool:
    """Re-check a cert's evidence snapshot against the docs/TRADING.md
    defaults, ignoring env. Legacy rows granted before RELAXED_MARKER existed
    are caught here too, so no data migration is needed to make mainnet safe.
    """
    if cert_is_relaxed(cert):
        return False
    try:
        if int(getattr(cert, "observation_days", 0) or 0) < DEFAULT_MIN_OBSERVATION_DAYS:
            return False
        if int(getattr(cert, "n_outcomes", 0) or 0) < DEFAULT_MIN_OUTCOMES:
            return False
        wr = getattr(cert, "win_rate", None)
        if wr is None or Decimal(str(wr)) < DEFAULT_MIN_WIN_RATE:
            return False
        pnl = getattr(cert, "total_pnl_usd", None)
        if pnl is None or Decimal(str(pnl)) < DEFAULT_MIN_TOTAL_PNL_USD:
            return False
        dd = getattr(cert, "max_drawdown_pct", None)
        if dd is None or Decimal(str(dd)) > DEFAULT_MAX_DRAWDOWN_PCT:
            return False
    except (TypeError, ValueError, ArithmeticError):
        return False
    return True


def cert_eligibility_thresholds() -> dict[str, int | Decimal]:
    """Thresholds for maybe_grant_certificate / evaluate_eligibility.

    Env overrides apply on testnet/paper only. On mainnet the docs/TRADING.md
    defaults are returned regardless of env (logged once per call).
    """
    active = cert_overrides_active()
    if active and is_mainnet():
        logger.warning(
            "mainnet venue configured; ignoring cert threshold overrides {}",
            active,
        )
        active = []

    def _int(name: str, default: int) -> int:
        raw = os.environ.get(name, "").strip() if name in active else ""
        return int(raw) if raw else default

    def _dec(name: str, default: Decimal) -> Decimal:
        raw = os.environ.get(name, "").strip() if name in active else ""
        return Decimal(raw) if raw else default

    return {
        "min_observation_days": _int(_ENV_MIN_OBSERVATION_DAYS, DEFAULT_MIN_OBSERVATION_DAYS),
        "min_outcomes": _int(_ENV_MIN_OUTCOMES, DEFAULT_MIN_OUTCOMES),
        "min_win_rate": _dec(_ENV_MIN_WIN_RATE, DEFAULT_MIN_WIN_RATE),
        "min_total_pnl_usd": _dec(_ENV_MIN_TOTAL_PNL_USD, DEFAULT_MIN_TOTAL_PNL_USD),
        "max_drawdown_pct": _dec(_ENV_MAX_DRAWDOWN_PCT, DEFAULT_MAX_DRAWDOWN_PCT),
        "min_ci_lower_usd": _dec(_ENV_MIN_CI_LOWER_USD, DEFAULT_MIN_CI_LOWER_USD),
    }

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
    min_ci_lower_usd: Decimal = DEFAULT_MIN_CI_LOWER_USD,
) -> EligibilityVerdict:
    """Compute paper-trade metrics for a strategy/version and return verdict.

    NOTE: This is the analysis step, not the grant. Granting writes a row
    via a separate code path (auto-grant or manual review). This function
    is what that grant path consults.

    Drawdown is approximated as max cumulative loss from a running peak,
    using outcome.pnl_usd ordered by observed_at. Good enough for the
    early-Phase paper data; can be tightened later.

    Sample size, win rate and the CI are counted in **episodes**, not
    outcomes (`edge_study.episode_groups`): fills of the same (strategy,
    symbol, side) whose signals re-emitted inside the first one's horizon are
    one bet, and their dollars are summed into it. Counting them separately
    let one call earn several "independent" wins toward `min_outcomes` and
    shrank the CI by the square root of the duplication. Total PnL and
    drawdown stay on the dollar path, which the duplicates really did move.
    """
    from matrix_shared.edge_study import episode_pnls

    async with shared_session_scope() as session:
        stmt = (
            select(
                Outcome.pnl_usd,
                Outcome.observed_at,
                Prediction.strategy_id,
                Prediction.asset_class,
                Prediction.symbol,
                Prediction.side,
                Prediction.generated_at,
                Prediction.horizon_seconds,
            )
            .join(Prediction, Prediction.id == Outcome.prediction_id)
            .where(Prediction.strategy_id == strategy_id)
            .where(Prediction.asset_class == asset_class)
            .where(Prediction.strategy_version == version)
            .where(Outcome.reason != "orphan_flat_close")  # flat-closes are not evidence
            .order_by(Outcome.observed_at.asc())
        )
        rows = list((await session.execute(stmt)).all())

    n_raw = len(rows)
    if n_raw == 0:
        return EligibilityVerdict(
            eligible=False,
            reasons=["no outcomes recorded"],
            metrics={"n_outcomes": 0, "n_outcomes_raw": 0, "observation_days": 0},
        )

    first_ts = rows[0].observed_at
    last_ts = rows[-1].observed_at
    observation_days = max(0, (last_ts - first_ts).days)

    bets = [Decimal(str(p)) for p in episode_pnls(
        sorted((r._asdict() for r in rows), key=lambda r: r["generated_at"])
    )]
    n = len(bets)
    wins = sum(1 for p in bets if p > 0)
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

    # 95% CI lower bound on mean pnl per episode (normal approx; n >= 2).
    if n >= 2:
        var = sum((p - avg_pnl) ** 2 for p in bets) / Decimal(n - 1)
        se = (var / Decimal(n)).sqrt() if var > 0 else Decimal("0")
        ci_lower = avg_pnl - Decimal("1.96") * se
    else:
        ci_lower = avg_pnl

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
    if ci_lower < min_ci_lower_usd:
        reasons.append(f"ci_lower_usd={ci_lower:.4f} < {min_ci_lower_usd} (mean not significantly positive)")

    return EligibilityVerdict(
        eligible=len(reasons) == 0,
        reasons=reasons,
        metrics={
            "n_outcomes": n,
            "n_outcomes_raw": n_raw,
            "observation_days": observation_days,
            "win_rate": str(win_rate.quantize(Decimal("0.000001"))),
            "avg_pnl_usd": str(avg_pnl.quantize(Decimal("0.000001"))),
            "total_pnl_usd": str(total_pnl.quantize(Decimal("0.000001"))),
            "max_drawdown_pct": str(dd_pct.quantize(Decimal("0.000001"))),
            "ci_lower_usd": str(ci_lower.quantize(Decimal("0.000001"))),
        },
    )


# Auto-grant defaults. A cert that's only granted once and never re-evaluated
# is misleading — paper performance drifts. Re-certification is the design.
GRANT_VALIDITY_DAYS = 7
DEFAULT_GRANTED_BY = "auto-eligibility"


async def maybe_grant_certificate(
    strategy_id: str,
    asset_class: str,
    version: int,
    *,
    granted_by: str = DEFAULT_GRANTED_BY,
    validity_days: int = GRANT_VALIDITY_DAYS,
    min_observation_days: int | None = None,
    min_outcomes: int | None = None,
    min_win_rate: Decimal | None = None,
    min_total_pnl_usd: Decimal | None = None,
    max_drawdown_pct: Decimal | None = None,
    min_ci_lower_usd: Decimal | None = None,
) -> tuple[bool, EligibilityVerdict | None, str]:
    """Idempotent auto-grant. Returns (granted, verdict, reason).

    Behavior:
      - Existing 'granted' cert with validity_until > now → (False, None, 'already granted').
      - No cert / pending / revoked / expired → run evaluate_eligibility.
        - Not eligible → (False, verdict, 'not eligible').
        - Eligible → UPSERT (insert new or update existing) with status='granted',
          fresh granted_at/validity_until, metrics snapshot. Returns (True, verdict, 'granted').

    Concurrency: relies on the unique constraint (strategy_id, asset_class, version)
    plus SELECT-then-INSERT/UPDATE within a single session_scope. Two racing grants
    will produce one row; the loser sees a unique-violation and is treated as a no-op.
    """
    # Pre-check existing cert before doing the (more expensive) eligibility eval.
    async with shared_session_scope() as session:
        stmt = (
            select(PaperTradeCertificate)
            .where(PaperTradeCertificate.strategy_id == strategy_id)
            .where(PaperTradeCertificate.asset_class == asset_class)
            .where(PaperTradeCertificate.version == version)
            .limit(1)
        )
        existing = (await session.execute(stmt)).scalar_one_or_none()
        now = datetime.now(timezone.utc)
        if (
            existing is not None
            and existing.status == "granted"
            and (existing.validity_until is None or existing.validity_until > now)
        ):
            return (False, None, "already granted")

    # Env overrides (MATRIX_CERT_*) apply unless caller passes explicit kwargs.
    # A grant that leaned on them is stamped RELAXED_MARKER so it can never
    # unlock mainnet execution (has_valid_certificate refuses it there).
    relaxed = bool(cert_overrides_active()) and not is_mainnet()
    kw: dict = dict(cert_eligibility_thresholds())
    if min_observation_days is not None:
        kw["min_observation_days"] = min_observation_days
    if min_outcomes is not None:
        kw["min_outcomes"] = min_outcomes
    if min_win_rate is not None:
        kw["min_win_rate"] = min_win_rate
    if min_total_pnl_usd is not None:
        kw["min_total_pnl_usd"] = min_total_pnl_usd
    if max_drawdown_pct is not None:
        kw["max_drawdown_pct"] = max_drawdown_pct
    if min_ci_lower_usd is not None:
        kw["min_ci_lower_usd"] = min_ci_lower_usd
    verdict = await evaluate_eligibility(strategy_id, asset_class, version, **kw)
    if not verdict.eligible:
        return (False, verdict, "not eligible")

    # Snapshot the verdict's metrics onto the cert row. evaluate_eligibility
    # returns strings; convert back to Decimal for the typed columns.
    m = verdict.metrics
    validity_until = now + timedelta(days=validity_days)

    async with shared_session_scope() as session:
        # Re-fetch inside the writing session to avoid double-grant race.
        stmt = (
            select(PaperTradeCertificate)
            .where(PaperTradeCertificate.strategy_id == strategy_id)
            .where(PaperTradeCertificate.asset_class == asset_class)
            .where(PaperTradeCertificate.version == version)
            .limit(1)
        )
        row = (await session.execute(stmt)).scalar_one_or_none()
        if row is None:
            row = PaperTradeCertificate(
                strategy_id=strategy_id,
                asset_class=asset_class,
                version=version,
            )
            session.add(row)
        row.status = "granted"
        row.granted_at = now
        row.granted_by = relaxed_granted_by(granted_by) if relaxed else granted_by
        row.validity_until = validity_until
        row.revoked_at = None
        row.revoked_reason = None
        row.n_outcomes = int(m["n_outcomes"])
        row.observation_days = int(m["observation_days"])
        row.win_rate = Decimal(str(m["win_rate"]))
        row.avg_pnl_usd = Decimal(str(m["avg_pnl_usd"]))
        row.total_pnl_usd = Decimal(str(m["total_pnl_usd"]))
        row.max_drawdown_pct = Decimal(str(m["max_drawdown_pct"]))

    return (True, verdict, "granted")


async def revoke_breached_certificates(*, max_drawdown_pct: Decimal | None = None) -> list[str]:
    """Revoke GRANTED certs whose strategy version has since breached the
    drawdown cap or turned to a negative CI (docs/AUTONOMY_PLAN.md P1.6).
    Re-grant is automatic once metrics recover (7-day validity cycle). Returns
    "strategy/asset_class/vN" for each revocation."""
    thresholds = cert_eligibility_thresholds()
    dd_cap = max_drawdown_pct if max_drawdown_pct is not None else thresholds["max_drawdown_pct"]
    revoked: list[str] = []
    async with shared_session_scope() as session:
        certs = list((await session.execute(
            select(PaperTradeCertificate).where(PaperTradeCertificate.status == "granted")
        )).scalars())
        for c in certs:
            session.expunge(c)
    for c in certs:
        verdict = await evaluate_eligibility(
            c.strategy_id, c.asset_class, c.version,
            min_observation_days=0, min_outcomes=0, min_win_rate=Decimal("0"),
            min_total_pnl_usd=Decimal("-1e12"), max_drawdown_pct=dd_cap,
            min_ci_lower_usd=Decimal("-1e12"),
        )
        breached = [r for r in verdict.reasons if r.startswith("max_drawdown_pct")]
        if not breached:
            continue
        async with shared_session_scope() as session:
            row = await session.get(PaperTradeCertificate, c.id)
            if row is None or row.status != "granted":
                continue
            row.status = "revoked"
            row.revoked_at = datetime.now(timezone.utc)
            row.revoked_reason = "auto: " + "; ".join(breached)[:300]
        key = f"{c.strategy_id}/{c.asset_class}/v{c.version}"
        revoked.append(key)
        logger.warning(f"certificate REVOKED {key}: {breached[0]}")
    return revoked

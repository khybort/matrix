"""The promotion bar: what a strategy must clear before it may hold capital.

Three ideas, all aimed at the same failure — a number that looked significant
because we went looking for it.

1. **Deflated Sharpe** (Bailey & López de Prado). A Sharpe ratio selected as the
   best of N trials is biased upward even when every strategy is worthless; the
   expected maximum of N draws from a zero-mean null is not zero. Deflation
   subtracts that expected maximum before asking for significance, and corrects
   for the non-normal shape of trade returns, which are skewed and fat-tailed
   exactly where it matters.

2. **Dependency-safe FDR**, in `edge_study.benjamini_yekutieli`.

3. **A pre-registered stopping rule.** The winner's curse says the edge we
   measured is an over-estimate of the edge we will get. So we register, at the
   moment a strategy first looks good, how many trades it needs for *half* that
   edge to still be detectable — and we commit to that number before seeing the
   data that would tempt us to move it. A strategy that never reaches its own
   number has not been proven; it has been waited on.

The registry is a JSON file on the shared model volume, one entry per
(strategy, market), written once and never silently rewritten: the whole point
is that the target was fixed in advance.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger

from matrix_shared.model_store import model_path

REGISTRY_PATH = model_path("edge_registry.json")

# The winner's-curse haircut: assume the true edge is this fraction of the
# measured one. Half is the conventional, deliberately harsh choice.
SHRINK = float(os.environ.get("MATRIX_PROMOTION_SHRINK", "0.5"))
ALPHA = float(os.environ.get("MATRIX_PROMOTION_ALPHA", "0.05"))
POWER = float(os.environ.get("MATRIX_PROMOTION_POWER", "0.80"))
# Cap so a near-zero shrunk edge cannot register an unreachable target and
# quietly park a strategy in "provisional" forever.
MAX_REQUIRED_N = int(os.environ.get("MATRIX_PROMOTION_MAX_N", "20000"))
# A floor on the registered target. Without it a large measured effect size
# registers a tiny target and "confirmed" arrives on a few dozen trades — which
# is exactly the small-sample result the whole bar exists to distrust.
MIN_REQUIRED_N = int(os.environ.get("MATRIX_PROMOTION_MIN_N", "200"))
# Deflated-Sharpe threshold for full confirmation. A strategy can reach its
# registered count and still not convince: bist_news_event hit n=33 against a
# 30-trade target on 2026-09-20 with a DSR of 0.53, i.e. a coin flip.
MIN_DSR = float(os.environ.get("MATRIX_PROMOTION_MIN_DSR", "0.95"))


def measurable_n() -> int:
    """The largest sample the edge study will actually simulate.

    A registered target above this can never be reached, so a strategy holding
    one would sit at `provisional` forever — the same shape as the bug found on
    2026-09-20, where momentum_xs registered 936 against a study cap of 800.
    Read from the environment rather than imported, because `edge_study`
    imports this module and the dependency must not run the other way."""
    return int(os.environ.get("MATRIX_EDGE_MAX_PER_STRATEGY", "1500"))

_EULER = 0.5772156649015329


def _ppf(p: float) -> float:
    """Inverse standard normal CDF (Acklam's rational approximation, ~1e-9)."""
    if not 0.0 < p < 1.0:
        raise ValueError("p must be in (0, 1)")
    a = [-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02,
         1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00]
    b = [-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02,
         6.680131188771972e01, -1.328068155288572e01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00,
         -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00,
         3.754408661907416e00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
           (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def _cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def moments(xs: list[float]) -> tuple[float, float, float, float]:
    """(mean, sd, skewness, kurtosis) — kurtosis is the raw fourth moment, so a
    normal sample gives 3.0, matching the deflated-Sharpe formula's convention."""
    n = len(xs)
    if n < 2:
        return (xs[0] if xs else 0.0, 0.0, 0.0, 3.0)
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    sd = math.sqrt(var)
    if sd == 0.0:
        return (m, 0.0, 0.0, 3.0)
    skew = sum(((x - m) / sd) ** 3 for x in xs) / n
    kurt = sum(((x - m) / sd) ** 4 for x in xs) / n
    return (m, sd, skew, kurt)


def expected_max_sharpe(n_trials: int, sr_variance: float) -> float:
    """Expected maximum Sharpe across `n_trials` worthless strategies.

    This is the number a backtest has to beat before "significant" means
    anything: search hard enough and something always looks good."""
    if n_trials <= 1 or sr_variance <= 0.0:
        return 0.0
    n = float(n_trials)
    gumbel = (1 - _EULER) * _ppf(1 - 1.0 / n) + _EULER * _ppf(1 - 1.0 / (n * math.e))
    return math.sqrt(sr_variance) * gumbel


def deflated_sharpe(
    returns: list[float], *, n_trials: int, sr_benchmark: float | None = None
) -> dict | None:
    """Probability the per-trade Sharpe is real after deflating for selection.

    `returns` are per-trade returns in any consistent unit (we pass bps). The
    Sharpe here is per trade, not annualised — annualising would require an
    assumption about trade frequency that our cadence does not support, and the
    hypothesis being tested ("is the edge real") does not need it.

    Returns None when the sample cannot support the question.
    """
    n = len(returns)
    if n < 20:
        return None
    mean, sd, skew, kurt = moments(returns)
    if sd <= 0.0:
        return None
    sr = mean / sd
    # Variance of the Sharpe estimator across trials, under the null that they
    # are all worthless: 1/(n-1) is the standard first-order approximation.
    sr0 = sr_benchmark if sr_benchmark is not None else expected_max_sharpe(
        n_trials, 1.0 / max(1, n - 1)
    )
    denom = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr
    if denom <= 0.0:
        return None
    z = (sr - sr0) * math.sqrt(n - 1) / math.sqrt(denom)
    return {
        "sharpe": sr,
        "sharpe_null": sr0,
        "skew": skew,
        "kurtosis": kurt,
        "n": n,
        "dsr": _cdf(z),
        "z": z,
    }


def required_trades(
    edge_bps: float,
    sd_bps: float,
    *,
    shrink: float = SHRINK,
    alpha: float = ALPHA,
    power: float = POWER,
    control_ratio: float = 20.0,
) -> int | None:
    """Trades needed for a `shrink`-ed edge to still be detectable.

    Standard two-sample size at unequal allocation: the control arm draws
    `control_ratio` samples per treatment sample, so its contribution to the
    standard error is small and the answer sits close to the one-sample figure.
    Registered once, in advance, so that a disappointing run cannot be rescued
    by moving the goalposts.
    """
    if sd_bps <= 0.0 or edge_bps <= 0.0:
        return None
    d = (edge_bps * shrink) / sd_bps
    if d <= 0.0:
        return None
    z = _ppf(1.0 - alpha / 2.0) + _ppf(power)
    n = (z / d) ** 2 * (1.0 + 1.0 / max(1e-9, control_ratio))
    return min(MAX_REQUIRED_N, max(MIN_REQUIRED_N, int(math.ceil(n))))


@dataclass
class Registration:
    strategy_id: str
    asset_class: str
    registered_at: str
    edge_bps: float
    sd_bps: float
    required_n: int
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "strategy_id": self.strategy_id,
            "asset_class": self.asset_class,
            "registered_at": self.registered_at,
            "edge_bps": self.edge_bps,
            "sd_bps": self.sd_bps,
            "required_n": self.required_n,
            "note": self.note,
        }


@dataclass
class Registry:
    entries: dict[str, dict] = field(default_factory=dict)

    @staticmethod
    def key(strategy_id: str, asset_class: str) -> str:
        return f"{strategy_id}/{asset_class}"

    @classmethod
    def load(cls, path: Path = REGISTRY_PATH) -> "Registry":
        try:
            return cls(entries=json.loads(path.read_text()))
        except FileNotFoundError:
            return cls()
        except Exception as e:  # noqa: BLE001 — a corrupt registry must not stop trading
            logger.warning(f"edge registry unreadable ({e}); starting empty")
            return cls()

    def save(self, path: Path = REGISTRY_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.entries, indent=2, sort_keys=True))
        tmp.replace(path)

    def get(self, strategy_id: str, asset_class: str) -> dict | None:
        return self.entries.get(self.key(strategy_id, asset_class))

    def register(
        self, strategy_id: str, asset_class: str, *, edge_bps: float, sd_bps: float
    ) -> dict | None:
        """Record the target the first time a strategy looks good. Never
        overwrites: a pre-registration that moves is not a pre-registration."""
        k = self.key(strategy_id, asset_class)
        if k in self.entries:
            return self.entries[k]
        need = required_trades(edge_bps, sd_bps)
        if need is None:
            return None
        reg = Registration(
            strategy_id=strategy_id,
            asset_class=asset_class,
            registered_at=datetime.now(UTC).isoformat(),
            edge_bps=round(edge_bps, 2),
            sd_bps=round(sd_bps, 2),
            required_n=need,
        )
        self.entries[k] = reg.as_dict()
        logger.info(
            f"pre-registered {k}: needs n>={need} for half of {edge_bps:.1f} bps "
            f"(sd {sd_bps:.1f}) to stay significant"
        )
        return self.entries[k]


def status(
    row: dict | None,
    *,
    registry: Registry,
    n_trials: int,
    significant: bool,
    min_dsr: float = MIN_DSR,
) -> str:
    """`confirmed` | `provisional` | `failed` | `unproven`.

    - `confirmed`  — cleared the dependency-safe FDR bar, reached its own
                     pre-registered trade count, AND survived deflation for the
                     number of strategies we searched. Only this earns full size.
    - `provisional`— looks good on at least one of those and has not yet failed.
    - `failed`     — reached its registered count and no longer clears the bar.
                     The hypothesis had its chance.
    - `unproven`   — nothing measured, or nothing significant.
    - `unprovable` — the sample its own edge demands is larger than the study
                     can ever simulate. Not pending: answered.

    All three conditions are required together on purpose. Significance alone
    is a p-value from one of thirteen overlapping tests; the count alone can be
    reached by a strategy whose effect size registered a small target; and a
    high DSR on forty trades is still forty trades.
    """
    if not row:
        return "unproven"
    n = int(row.get("n") or 0)
    dsr = row.get("dsr")
    reg = registry.get(row.get("strategy", ""), row.get("market", ""))
    if reg is None:
        return "provisional" if significant else "unproven"
    need = int(reg["required_n"])
    # A target the study can never measure is not a pending target, it is an
    # answer: this edge is too small to prove with the data we can hold.
    # Saying so beats leaving the strategy `provisional` for ever.
    if need > measurable_n():
        return "unprovable"
    reached = n >= need
    convincing = dsr is not None and float(dsr) >= min_dsr
    if not reached:
        return "provisional" if significant else "unproven"
    if significant and convincing:
        return "confirmed"
    if significant:
        return "provisional"   # enough trades, not yet convincing after deflation
    return "failed"


__all__ = [
    "MIN_DSR",
    "measurable_n",
    "MIN_REQUIRED_N",
    "REGISTRY_PATH",
    "Registration",
    "Registry",
    "deflated_sharpe",
    "expected_max_sharpe",
    "moments",
    "required_trades",
    "status",
]

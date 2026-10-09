"""Shrinkage and confidence bounds for per-episode results.

Pure functions shared by the learning loop's selectors (labs evolution and
promotion, reflection's mutation gate). Every one of them used to act on a raw
mean over a handful of episodes, which ranks luck: with a per-episode score sd
of ~0.65 a five-episode mean has a standard error of ~0.29, larger than any
edge this system has ever measured (docs/wiki/learning-loop-statistics.md).

Two tools:

- `MeanEvidence` — mean, sd and Student-t bounds of one sample of per-episode
  values (one value per episode, `edge_study.episode_groups`).
- `eb_prior` / `eb_posterior` — normal-normal empirical Bayes over a population
  of such samples: estimate how much the true means really differ (tau²,
  DerSimonian-Laird) and shrink each sample's mean toward the population mean
  by its own noise. When the population shows no dispersion beyond noise,
  tau² = 0 and every member's posterior is the population mean: ranking them
  is then (correctly) a coin toss.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from statistics import NormalDist

_N = NormalDist()


def t_quantile(p: float, df: int) -> float:
    """Student-t quantile (Hill 1970 expansion, |error| < 1e-3 for df >= 3).

    No scipy in the service images; the bounds here gate decisions at n >= 10,
    where the expansion is far more accurate than the decision is sensitive.
    """
    z = _N.inv_cdf(p)
    if df <= 0:
        return math.inf if p > 0.5 else -math.inf
    if df == 1:
        return math.tan(math.pi * (p - 0.5))
    if df == 2:
        a = 2 * p - 1
        return a * math.sqrt(2 / (1 - a * a))
    g1 = (z**3 + z) / 4
    g2 = (5 * z**5 + 16 * z**3 + 3 * z) / 96
    g3 = (3 * z**7 + 19 * z**5 + 17 * z**3 - 15 * z) / 384
    g4 = (79 * z**9 + 776 * z**7 + 1482 * z**5 - 1920 * z**3 - 945 * z) / 92160
    return z + g1 / df + g2 / df**2 + g3 / df**3 + g4 / df**4


@dataclass(frozen=True, slots=True)
class MeanEvidence:
    """One sample of per-episode values: n, mean, sample sd (n-1)."""

    n: int
    mean: float
    sd: float

    @classmethod
    def from_values(cls, xs: Iterable[float]) -> MeanEvidence:
        vals = [float(x) for x in xs]
        n = len(vals)
        if n == 0:
            return cls(0, 0.0, 0.0)
        mean = sum(vals) / n
        var = sum((x - mean) ** 2 for x in vals) / (n - 1) if n > 1 else 0.0
        return cls(n, mean, math.sqrt(var))

    @property
    def se(self) -> float:
        return self.sd / math.sqrt(self.n) if self.n > 1 else math.inf

    @property
    def t(self) -> float:
        """One-sample t of the mean against zero (0 when undefined)."""
        se = self.se
        return self.mean / se if self.n > 1 and se > 0 else 0.0

    def upper(self, conf: float = 0.95) -> float:
        """One-sided upper confidence bound of the mean (+inf when n < 2)."""
        if self.n < 2:
            return math.inf
        return self.mean + t_quantile(conf, self.n - 1) * self.se

    def lower(self, conf: float = 0.95) -> float:
        """One-sided lower confidence bound of the mean (-inf when n < 2)."""
        if self.n < 2:
            return -math.inf
        return self.mean - t_quantile(conf, self.n - 1) * self.se


@dataclass(frozen=True, slots=True)
class EBPrior:
    """Population of true means ~ N(mu0, tau2); per-episode noise variance sigma2."""

    mu0: float
    tau2: float
    sigma2: float
    k: int  # members the prior was estimated from
    mu0_var: float = 0.0  # sampling variance of mu0 itself


def pooled_variance(samples: Sequence[MeanEvidence]) -> float:
    """Within-sample variance pooled over members (weights n-1)."""
    num = sum((s.n - 1) * s.sd**2 for s in samples if s.n > 1)
    den = sum(s.n - 1 for s in samples if s.n > 1)
    return num / den if den > 0 else 0.0


def eb_prior(samples: Sequence[MeanEvidence], *, sigma2: float | None = None) -> EBPrior | None:
    """DerSimonian-Laird estimate of (mu0, tau2) over members with n >= 2.

    Each member's sampling variance is the POOLED sigma² / n, not its own sd:
    a five-episode sd is itself noise, and a lucky low-variance member would
    otherwise get both a high weight and a tight bound. None with < 3 members.
    """
    xs = [s for s in samples if s.n >= 2]
    if len(xs) < 3:
        return None
    s2 = pooled_variance(xs) if sigma2 is None else sigma2
    if s2 <= 0:
        return None
    w = [s.n / s2 for s in xs]
    sw = sum(w)
    mu_fe = sum(wi * s.mean for wi, s in zip(w, xs)) / sw
    q = sum(wi * (s.mean - mu_fe) ** 2 for wi, s in zip(w, xs))
    c = sw - sum(wi * wi for wi in w) / sw
    tau2 = max(0.0, (q - (len(xs) - 1)) / c) if c > 0 else 0.0
    w_re = [1.0 / (s2 / s.n + tau2) for s in xs]
    mu0 = sum(wi * s.mean for wi, s in zip(w_re, xs)) / sum(w_re)
    return EBPrior(mu0=mu0, tau2=tau2, sigma2=s2, k=len(xs), mu0_var=1.0 / sum(w_re))


def eb_posterior(s: MeanEvidence, prior: EBPrior) -> tuple[float, float]:
    """(posterior mean, posterior sd) of one member's true mean.

    B = tau² / (tau² + sigma²/n) is how far the member's own mean is trusted;
    with tau² = 0 it is 0 and the posterior is the population mean. The sd
    carries the uncertainty of mu0 too, weighted by (1 - B)²: a population
    that merely had a good week must not read as a confident edge.
    """
    if s.n <= 0:
        return prior.mu0, math.sqrt(prior.tau2 + prior.mu0_var)
    v = prior.sigma2 / s.n
    b = prior.tau2 / (prior.tau2 + v) if prior.tau2 + v > 0 else 0.0
    var = b * v + (1 - b) ** 2 * prior.mu0_var
    return prior.mu0 + b * (s.mean - prior.mu0), math.sqrt(var)

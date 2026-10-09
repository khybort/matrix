"""Statistics of the research protocol: cluster-robust t, Student t tail, BHY q.

Pure Python on purpose: the harness runs in the backtest and dev_agent images,
neither of which ships numpy or scipy.

One sample per episode, standard error clustered by ISO week (default) or UTC
day, because episodes that overlap in calendar time share the market they were
taken in. One-sided p from Student t with G - 1 degrees of freedom (G = number
of clusters), the same arithmetic every 2026-10 round used by hand.
"""

from __future__ import annotations

import math
from collections.abc import Hashable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone

UTC = timezone.utc  # noqa: UP017 — datetime.UTC is 3.11+, the host python (pandas) is 3.9


def _betacf(a: float, b: float, x: float) -> float:
    qab, qap, qam = a + b, a + 1, a - 1
    c, d = 1.0, 1 - qab * x / qap
    d = 1 / d if abs(d) > 1e-30 else 1e30
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1 + aa * d
        c = 1 + aa / c
        d = 1 / d if abs(d) > 1e-30 else 1e30
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1 + aa * d
        c = 1 + aa / c
        d = 1 / d if abs(d) > 1e-30 else 1e30
        de = d * c
        h *= de
        if abs(de - 1) < 1e-12:
            break
    return h


def t_sf(t: float, df: float) -> float:
    """One-sided upper tail P(T >= t) of Student's t with `df` degrees of freedom."""
    if not math.isfinite(t) or df <= 0:
        if t == math.inf:
            return 0.0
        if t == -math.inf:
            return 1.0
        return math.nan
    x = df / (df + t * t)
    if x >= 1.0:
        return 0.5
    lb = math.lgamma(df / 2 + 0.5) - math.lgamma(df / 2) - math.lgamma(0.5)
    bt = math.exp(lb + (df / 2) * math.log(x) + 0.5 * math.log(1 - x))
    if x < (df / 2 + 1) / (df / 2 + 0.5 + 2):
        ib = bt * _betacf(df / 2, 0.5, x) / (df / 2)
    else:
        ib = 1 - bt * _betacf(0.5, df / 2, 1 - x) / 0.5
    return ib / 2 if t > 0 else 1 - ib / 2


def week_key(ts: datetime) -> str:
    """ISO week (Monday..Sunday, UTC) — the same partition as pandas' 'W' period."""
    y, w, _ = ts.astimezone(UTC).isocalendar()
    return f"{y}-W{w:02d}"


def day_key(ts: datetime) -> str:
    return ts.astimezone(UTC).date().isoformat()


CLUSTERS = {"week": week_key, "day": day_key}


@dataclass(frozen=True)
class Clustered:
    n: int
    mean: float
    median: float
    t: float
    clusters: int
    p: float  # one-sided, H0: mean <= 0

    def as_dict(self) -> dict:
        return {
            "n": self.n,
            "mean": _r(self.mean, 2),
            "median": _r(self.median, 2),
            "t": _r(self.t, 3),
            "clusters": self.clusters,
            "p": self.p if math.isfinite(self.p) else None,
        }


def _r(x: float, nd: int) -> float | None:
    return round(x, nd) if math.isfinite(x) else None


def median(xs: Sequence[float]) -> float:
    s = sorted(xs)
    n = len(s)
    if n == 0:
        return math.nan
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def clustered_t(values: Iterable[float], clusters: Iterable[Hashable]) -> Clustered:
    """Mean with a cluster-robust (CR1, G/(G-1)) standard error.

    se = sqrt(G/(G-1) * sum_g (sum_{i in g} (x_i - mean))^2) / n.
    Non-finite values are dropped with their cluster label. Fewer than 3
    samples or a single cluster gives t = nan, p = nan (no decision possible).
    """
    zipped = zip(values, clusters)  # noqa: B905 — strict= is 3.10+, the host python is 3.9
    pairs = [(float(v), c) for v, c in zipped if math.isfinite(float(v))]
    n = len(pairs)
    if n < 3:
        mu = sum(v for v, _ in pairs) / n if n else math.nan
        return Clustered(n, mu, median([v for v, _ in pairs]), math.nan, 0, math.nan)
    mu = sum(v for v, _ in pairs) / n
    sums: dict[Hashable, float] = {}
    for v, c in pairs:
        sums[c] = sums.get(c, 0.0) + (v - mu)
    g = len(sums)
    if g < 2:
        return Clustered(n, mu, median([v for v, _ in pairs]), math.nan, g, math.nan)
    se = math.sqrt(sum(s * s for s in sums.values()) * g / (g - 1)) / n
    t = mu / se if se > 0 else math.nan
    p = t_sf(t, g - 1) if math.isfinite(t) else math.nan
    return Clustered(n, mu, median([v for v, _ in pairs]), t, g, p)


def harmonic(m: int) -> float:
    return sum(1.0 / i for i in range(1, m + 1))


def bhy_qvalues(pvalues: Sequence[float], m: int | None = None) -> list[float]:
    """Benjamini-Yekutieli adjusted p-values (q), valid under arbitrary dependence.

    q_(i) = min_{k >= i} min(1, p_(k) * m * H(m) / k). `m` defaults to
    len(pvalues); a larger m treats the missing tests as p = 1. A test is a
    discovery at FDR level alpha iff q <= alpha — the same set
    `edge_study.benjamini_yekutieli` returns.
    """
    k = len(pvalues)
    m = k if m is None else m
    if m < k:
        raise ValueError(f"m={m} smaller than the {k} p-values given")
    if k == 0:
        return []
    c = harmonic(m)
    order = sorted(range(k), key=lambda i: (pvalues[i], i))
    q = [1.0] * k
    running = 1.0
    for rank in range(k, 0, -1):
        i = order[rank - 1]
        running = min(running, min(1.0, pvalues[i] * m * c / rank))
        q[i] = running
    return q

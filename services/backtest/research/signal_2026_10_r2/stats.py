"""Shared statistics: day-clustered t, BY q-values."""
import math
import numpy as np


def clustered_t(x, groups):
    x = np.asarray(x, float)
    n = len(x)
    if n < 2:
        return float("nan")
    m = x.mean()
    r = x - m
    _, inv = np.unique(np.asarray(groups), return_inverse=True)
    s = np.bincount(inv, weights=r)
    G = len(s)
    if G < 2:
        return float("nan")
    var = (s ** 2).sum() / n ** 2 * G / (G - 1)
    return m / math.sqrt(var) if var > 0 else float("nan")


def p_one_sided(t):
    return 0.5 * math.erfc(t / math.sqrt(2)) if t == t else 1.0


def by_qvalues(p):
    p = np.asarray(p, float)
    m = len(p)
    c = sum(1.0 / i for i in range(1, m + 1))
    o = np.argsort(p)
    q = np.empty(m)
    prev = 1.0
    for rank in range(m, 0, -1):
        i = o[rank - 1]
        prev = min(prev, p[i] * m * c / rank)
        q[i] = prev
    return q


def summary(x, days, weeks=None):
    x = np.asarray(x, float)
    out = {"n": len(x), "days": len(set(days)), "mean": float(x.mean()) if len(x) else float("nan"),
           "median": float(np.median(x)) if len(x) else float("nan"), "t_day": clustered_t(x, days)}
    if weeks is not None:
        out["t_week"] = clustered_t(x, weeks)
    return out

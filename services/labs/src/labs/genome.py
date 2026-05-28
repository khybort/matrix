"""Genome — the parameter set for a candidate trading-decision algorithm.

A genome encodes:
    weights: dict[feature_name, weight]  — must sum to 1.0 after normalization
    signal_threshold: Decimal — minimum |score| to emit a non-hold signal
    horizon_seconds: int — how long to hold the hypothetical position

Operators:
    random()         — sample a fresh genome
    crossover(a, b)  — produce a child by mixing parents
    mutate(g)        — apply gaussian noise to parameters
    encode/decode    — JSON <-> Genome (for DB storage)
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

FEATURES = ("trade_flow", "funding", "oi_delta", "ob_imbalance", "news")

# Mutation hyperparameters
WEIGHT_MUT_SIGMA = 0.08
THRESHOLD_MUT_SIGMA = 0.04
HORIZON_MUT_SIGMA = 180  # seconds (was 30; needs to traverse a wider range)

# Bounds — 2026-05-29: was [30, 600]. All converged genomes sat at 199-224s and
# bled paper money because slippage > signal at <5min horizons. Memory of
# matrix_agent alpha diagnosis: direction works at 1800s+, breaks down below.
# Expanding floor + ceiling so evolution can find the actual signal regime.
THRESHOLD_MIN = Decimal("0.05")
THRESHOLD_MAX = Decimal("0.50")
HORIZON_MIN = 600
HORIZON_MAX = 3600
WEIGHT_MIN_EACH = Decimal("0.01")  # avoid zero-weight features


@dataclass(slots=True)
class Genome:
    weights: dict[str, Decimal] = field(default_factory=dict)
    signal_threshold: Decimal = Decimal("0.18")
    horizon_seconds: int = 1800

    def to_dict(self) -> dict[str, Any]:
        return {
            "weights": {k: str(v) for k, v in self.weights.items()},
            "signal_threshold": str(self.signal_threshold),
            "horizon_seconds": self.horizon_seconds,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Genome:
        w = {k: Decimal(str(v)) for k, v in d.get("weights", {}).items()}
        # Make sure all standard features are present
        for f in FEATURES:
            w.setdefault(f, Decimal("0"))
        return cls(
            weights=w,
            signal_threshold=Decimal(str(d.get("signal_threshold", "0.18"))),
            horizon_seconds=int(d.get("horizon_seconds", 1800)),
        )


def normalize_weights(weights: dict[str, Decimal]) -> dict[str, Decimal]:
    """Clamp min, then re-normalize so they sum to 1."""
    out = {k: max(WEIGHT_MIN_EACH, v) for k, v in weights.items()}
    s = sum(out.values(), Decimal("0"))
    if s <= 0:
        # all zeroed somehow → uniform
        u = Decimal("1") / Decimal(len(out))
        return {k: u for k in out}
    return {k: v / s for k, v in out.items()}


def clamp_threshold(t: Decimal) -> Decimal:
    return max(THRESHOLD_MIN, min(THRESHOLD_MAX, t))


def clamp_horizon(h: int) -> int:
    return max(HORIZON_MIN, min(HORIZON_MAX, int(h)))


def random_genome(rng: random.Random | None = None) -> Genome:
    rng = rng or random
    raw_w = {f: Decimal(str(rng.uniform(0.05, 0.5))) for f in FEATURES}
    g = Genome(
        weights=normalize_weights(raw_w),
        signal_threshold=clamp_threshold(Decimal(str(rng.uniform(0.10, 0.30)))),
        horizon_seconds=clamp_horizon(rng.randint(900, 2700)),
    )
    return g


def crossover(a: Genome, b: Genome, rng: random.Random | None = None) -> Genome:
    """Uniform crossover for weights; coin-flip for scalars."""
    rng = rng or random
    new_weights: dict[str, Decimal] = {}
    for f in FEATURES:
        if rng.random() < 0.5:
            new_weights[f] = a.weights.get(f, Decimal("0"))
        else:
            new_weights[f] = b.weights.get(f, Decimal("0"))
    thr = a.signal_threshold if rng.random() < 0.5 else b.signal_threshold
    hor = a.horizon_seconds if rng.random() < 0.5 else b.horizon_seconds
    return Genome(
        weights=normalize_weights(new_weights),
        signal_threshold=clamp_threshold(thr),
        horizon_seconds=clamp_horizon(hor),
    )


def mutate(g: Genome, rng: random.Random | None = None, *, mutation_rate: float = 0.6) -> Genome:
    """Gaussian perturbation to each parameter with probability mutation_rate."""
    rng = rng or random
    new_w: dict[str, Decimal] = {}
    for f, w in g.weights.items():
        if rng.random() < mutation_rate:
            delta = Decimal(str(rng.gauss(0, WEIGHT_MUT_SIGMA)))
            new_w[f] = w + delta
        else:
            new_w[f] = w
    new_w = normalize_weights(new_w)

    thr = g.signal_threshold
    if rng.random() < mutation_rate:
        thr = thr + Decimal(str(rng.gauss(0, THRESHOLD_MUT_SIGMA)))
    thr = clamp_threshold(thr)

    hor = g.horizon_seconds
    if rng.random() < mutation_rate:
        hor = hor + int(rng.gauss(0, HORIZON_MUT_SIGMA))
    hor = clamp_horizon(hor)

    return Genome(weights=new_w, signal_threshold=thr, horizon_seconds=hor)

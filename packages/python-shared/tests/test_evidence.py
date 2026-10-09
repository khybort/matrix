"""matrix_shared.evidence: t bounds and empirical-Bayes shrinkage."""

from __future__ import annotations

import math
import random

from matrix_shared.evidence import MeanEvidence, eb_posterior, eb_prior, t_quantile


def test_t_quantile_matches_tables():
    for df, ref in [(3, 2.353), (5, 2.015), (9, 1.833), (29, 1.699), (100, 1.660)]:
        assert abs(t_quantile(0.95, df) - ref) < 2e-3
    assert abs(t_quantile(0.975, 9) - 2.262) < 2e-3
    assert abs(t_quantile(0.95, 2) - 2.920) < 1e-3


def test_bounds():
    e = MeanEvidence.from_values([1, 2, 3, 4, 5])
    assert e.n == 5 and e.mean == 3 and abs(e.sd - math.sqrt(2.5)) < 1e-12
    assert e.lower() < e.mean < e.upper()
    assert MeanEvidence.from_values([1.0]).upper() == math.inf
    assert MeanEvidence.from_values([]).n == 0


def test_upper_bound_below_zero_is_rare_under_zero_mean():
    rng = random.Random(1)
    hits = sum(
        MeanEvidence.from_values(rng.gauss(0, 1) for _ in range(12)).upper(0.95) < 0
        for _ in range(4000)
    )
    assert 0.03 < hits / 4000 < 0.07


def test_eb_finds_real_dispersion_and_shrinks_small_samples_more():
    rng = random.Random(2)
    true = [rng.gauss(0, 0.3) for _ in range(60)]
    pool = [MeanEvidence.from_values(mu + rng.gauss(0, 0.5) for _ in range(40)) for mu in true]
    prior = eb_prior(pool)
    assert prior is not None and 0.03 < prior.tau2 < 0.2
    small = MeanEvidence(4, 0.6, 0.5)
    big = MeanEvidence(400, 0.6, 0.5)
    assert eb_posterior(small, prior)[0] < eb_posterior(big, prior)[0] < 0.6


def test_eb_needs_three_members():
    assert eb_prior([MeanEvidence(10, 0.1, 0.5)] * 2) is None

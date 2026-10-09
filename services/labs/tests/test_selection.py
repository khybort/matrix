"""labs.selection: shrunk ranking, breeding gate, pre-registered promotion looks."""

from __future__ import annotations

import random
from datetime import date, timedelta

from matrix_shared.evidence import EBPrior, MeanEvidence

from labs.selection import (
    checkpoint,
    excess_values,
    fit_prior,
    plan_generation,
    promotion_check,
    rank,
)
from labs.zero_edge_sim import run


def test_excess_is_against_the_other_genomes_in_the_same_hour():
    ex = excess_values({
        "a": [(1, 0.5), (2, 0.1)],
        "b": [(1, -0.5)],
        "c": [(1, 0.0), (3, 9.9)],  # hour 3 has no contemporary → dropped
    })
    assert ex["a"] == [0.5 - (-0.5 + 0.0) / 2]  # hour 2 alone too
    assert ex["b"] == [-0.5 - (0.5 + 0.0) / 2]
    assert ex["c"] == [0.0 - (0.5 - 0.5) / 2]


def test_no_dispersion_means_little_ranking_signal():
    # Thirty genomes drawn from one distribution: their means differ only by
    # noise, so the prior finds tau² ≈ 0 and the posteriors collapse toward
    # the population mean (exactly onto it when the estimate truncates at 0).
    rng = random.Random(7)
    pool = {i: MeanEvidence.from_values(rng.gauss(0, 0.6) for _ in range(20)) for i in range(30)}
    prior = fit_prior(pool.values())
    assert prior is not None and prior.tau2 < 0.36 / 20  # below one genome's sampling variance
    ranked = rank(pool, prior)
    raw_spread = max(e.mean for e in pool.values()) - min(e.mean for e in pool.values())
    post_spread = ranked[0].post_mean - ranked[-1].post_mean
    assert post_spread < 0.5 * raw_spread
    flat = rank(pool, EBPrior(mu0=0.0, tau2=0.0, sigma2=0.36, k=30))
    assert len({r.post_mean for r in flat}) == 1


def test_a_lucky_five_episode_genome_does_not_outrank_a_long_record():
    prior = EBPrior(mu0=0.0, tau2=0.01, sigma2=0.36, k=50)
    members = {
        "lucky": MeanEvidence(5, 0.40, 0.6),  # raw winner, se 0.27
        "steady": MeanEvidence(80, 0.12, 0.6),  # se 0.07
    }
    ranked = rank(members, prior)
    assert [r.id for r in ranked] == ["steady", "lucky"]
    lucky = next(r for r in ranked if r.id == "lucky")
    assert lucky.post_mean < 0.1  # shrunk most of the way to mu0


def test_breeding_needs_two_genomes_with_enough_episodes():
    prior = EBPrior(mu0=0.0, tau2=0.01, sigma2=0.36, k=50)
    members = {i: MeanEvidence(6, 0.1 * i, 0.6) for i in range(8)}
    members["veteran"] = MeanEvidence(40, 0.05, 0.6)
    parents, culled = plan_generation(rank(members, prior), elite_frac=0.25, cull_frac=0.4, min_breed=20)
    assert parents == []  # one breedable genome is not a pair → immigrants
    assert len(culled) == 3
    members["veteran2"] = MeanEvidence(25, 0.0, 0.6)
    parents, _ = plan_generation(rank(members, prior), elite_frac=0.25, cull_frac=0.4, min_breed=20)
    assert {p.id for p in parents} == {"veteran", "veteran2"}


def test_checkpoints():
    assert checkpoint(29) is None
    assert checkpoint(30) == 30 and checkpoint(59) == 30 and checkpoint(61) == 60
    assert checkpoint(10_000) == 240


def _days(n: int, per_day: int) -> list[date]:
    return [date(2026, 10, 1) + timedelta(days=i // per_day) for i in range(n)]


PRIOR = EBPrior(mu0=0.0, tau2=0.02, sigma2=0.36, k=200, mu0_var=0.0001)


def test_promotion_passes_on_a_real_edge():
    rng = random.Random(3)
    raw = [0.3 + rng.gauss(0, 0.2) for _ in range(40)]
    chk = promotion_check(_days(40, 8), raw, raw, PRIOR, min_fitness=0.05)
    assert chk.ok, chk.reason
    assert chk.k == 30 and chk.n_days == 4  # tested on the first 30 only


def test_promotion_rejects_one_afternoon_of_luck():
    raw = [0.3] * 15 + [0.4] * 15
    chk = promotion_check(_days(30, 30), raw, raw, PRIOR, min_fitness=0.05)
    assert not chk.ok and "day(s)" in chk.reason


def test_promotion_rejects_a_market_wide_good_week():
    # Made money, but no more than its contemporaries did: excess ≈ 0.
    rng = random.Random(5)
    raw = [0.3 + rng.gauss(0, 0.1) for _ in range(30)]
    excess = [rng.gauss(0, 0.3) for _ in range(30)]
    chk = promotion_check(_days(30, 6), raw, excess, PRIOR, min_fitness=0.05)
    assert not chk.ok and "excess" in chk.reason


def test_later_episodes_do_not_reopen_a_failed_look():
    # 59 episodes: tested on the first 30 only, so a strong tail is not used
    raw = [-0.1] * 30 + [0.5] * 29
    chk = promotion_check(_days(59, 6), raw, raw, PRIOR, min_fitness=0.05)
    assert chk.k == 30 and not chk.ok


def test_zero_edge_simulation_old_rule_promotes_noise_new_rule_almost_never():
    res = run(4, 10, "null", seed=11)
    assert res["old"]["false_per_30d"] > 5
    assert res["new"]["false_per_30d"] < res["old"]["false_per_30d"] / 10

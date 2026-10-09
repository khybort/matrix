"""Which genomes breed, which are culled, which may be promoted — on shrunk evidence.

Until 2026-10-09 evolution ranked every genome with >= 5 episodes on its raw
variance-penalised fitness and bred the top quarter. Five episodes of a score
whose sd is ~0.65 have a standard error of ~0.29; the genome that "won" was the
luckiest, the median genome lived 1.3 hours, and the ones that reached the
promotion bar got there on early luck that stayed in their mean
(docs/wiki/learning-loop-statistics.md). This module replaces that rule.

Lab genomes trade the same symbols in the same hours, so their episodes share
a market shock: in Sep 2026 the population-mean score moved with sd ~0.38
between hours and ~0.18 between days, against a per-episode sd of 0.66. Two
consequences shape everything here:

- **Rank on excess, not on raw score.** A genome's episode is compared with
  what the rest of the population made in the same hour (`excess_values`).
  Raw means rank *when* a genome was alive; that showed up as between-genome
  dispersion and made empirical Bayes trust it.
- **Shrink.** Excess means are ranked on their empirical-Bayes posterior
  (`matrix_shared.evidence`), pulled toward the population by each genome's
  own noise: a five-episode genome sits next to the population mean, only
  evidence moves it away. Breeding needs >= MIN_BREED_EPISODES; fewer than
  two such parents → the free places go to random immigrants, not to the
  offspring of noise.
- **Promote** on pre-registered looks (`checkpoint`) only when the genome
  (a) credibly beats its contemporaries (posterior excess lower bound > 0),
  (b) made money in absolute terms over day clusters (day-mean lower bound
  > 0 on >= PROMOTE_MIN_DAYS days — thirty episodes inside one afternoon are
  closer to one observation than to thirty) and (c) its raw mean clears the
  fitness bar.

Pure: no DB, so the live rule and the zero-edge simulation
(`labs.zero_edge_sim`) run the exact same code.
"""

from __future__ import annotations

import os
import zlib
from collections.abc import Hashable, Iterable, Sequence
from dataclasses import dataclass
from statistics import NormalDist

from matrix_shared.evidence import EBPrior, MeanEvidence, eb_posterior, eb_prior

MIN_RANK_EPISODES = 5  # enough to be culled
MIN_BREED_EPISODES = int(os.environ.get("MATRIX_LAB_MIN_BREED_EPISODES", "20"))
# One-sided confidence of both promotion bounds, and the minimum number of
# distinct UTC days the tested episodes must cover.
PROMOTE_CONF = float(os.environ.get("MATRIX_LAB_PROMOTE_CONF", "0.95"))
PROMOTE_MIN_DAYS = int(os.environ.get("MATRIX_LAB_PROMOTE_MIN_DAYS", "3"))
# Pre-registered looks: a genome is tested on exactly its first K episodes,
# K the largest checkpoint it has reached, so a scan every ten minutes does
# not turn one genome into a thousand tests (optional stopping).
PROMOTE_CHECKPOINTS: tuple[int, ...] = (30, 60, 120, 240)
_N = NormalDist()


def checkpoint(n: int, checkpoints: Sequence[int] = PROMOTE_CHECKPOINTS) -> int | None:
    """Largest checkpoint <= n, or None below the first."""
    reached = [k for k in checkpoints if k <= n]
    return max(reached) if reached else None


def bucket_totals(
    episodes: Iterable[Sequence[tuple[Hashable, float]]],
) -> tuple[dict[Hashable, float], dict[Hashable, int]]:
    """(sum, count) of episode values per time bucket over a population."""
    tot: dict[Hashable, float] = {}
    cnt: dict[Hashable, int] = {}
    for eps in episodes:
        for b, v in eps:
            tot[b] = tot.get(b, 0.0) + v
            cnt[b] = cnt.get(b, 0) + 1
    return tot, cnt


def excess_from_totals(
    eps: Sequence[tuple[Hashable, float]], tot: dict[Hashable, float], cnt: dict[Hashable, int]
) -> list[float]:
    """Each episode minus the mean of every OTHER episode in its bucket; an
    episode alone in its bucket has no contemporary and is dropped."""
    return [v - (tot[b] - v) / (cnt[b] - 1) for b, v in eps if cnt.get(b, 0) > 1]


def excess_values(
    episodes: dict[Hashable, Sequence[tuple[Hashable, float]]],
) -> dict[Hashable, list[float]]:
    """`excess_from_totals` for every genome of a population.

    `episodes` maps genome → [(bucket, value)] in time order, bucket = the UTC
    hour of the episode.
    """
    tot, cnt = bucket_totals(episodes.values())
    return {gid: excess_from_totals(eps, tot, cnt) for gid, eps in episodes.items()}


@dataclass(frozen=True, slots=True)
class Ranked:
    id: Hashable
    ev: MeanEvidence  # excess over contemporaries
    post_mean: float
    post_sd: float


def posterior(ev: MeanEvidence, prior: EBPrior | None) -> tuple[float, float]:
    """Shrunk (mean, sd); without a prior the raw mean and its standard error."""
    if prior is None:
        return ev.mean, ev.se
    return eb_posterior(ev, prior)


def fit_prior(pool: Iterable[MeanEvidence], *, min_rank: int = MIN_RANK_EPISODES) -> EBPrior | None:
    return eb_prior([e for e in pool if e.n >= min_rank])


def rank(
    members: dict[Hashable, MeanEvidence],
    prior: EBPrior | None,
    *,
    min_rank: int = MIN_RANK_EPISODES,
    salt: int = 0,
) -> list[Ranked]:
    """Members with >= min_rank episodes, best posterior mean first.

    The prior comes from `fit_prior` over recent genomes of the same asset
    class (members included). Without one (< 3 pool members, i.e. while a
    population is being seeded) nothing is shrunk.
    """
    out = [Ranked(gid, ev, *posterior(ev, prior)) for gid, ev in members.items() if ev.n >= min_rank]
    # Ties (tau² = 0: every posterior is mu0, the data show no dispersion)
    # break at random — `salt` changes every cycle — neither on luck
    # (n·(mean − mu0) let lucky genomes outlive the rest and doubled false
    # promotions in the zero-edge simulation), on age (insertion order culled
    # every newcomer first) nor on a fixed hash (some genomes never culled).
    out.sort(key=lambda r: (r.post_mean, zlib.crc32(f"{salt}:{r.id}".encode())), reverse=True)
    return out


def plan_generation(
    ranked: Sequence[Ranked],
    *,
    elite_frac: float,
    cull_frac: float,
    min_breed: int = MIN_BREED_EPISODES,
) -> tuple[list[Ranked], list[Ranked]]:
    """(parents, culled) from a best-first ranking.

    Parents: the top `elite_frac` (at least two) of the genomes with enough
    episodes to breed; an empty list when fewer than two qualify. Culled: the
    bottom `cull_frac` of everything ranked — retiring on noise only wastes a
    slot, breeding on noise compounds it, so the cull keeps the low bar.
    """
    breedable = [r for r in ranked if r.ev.n >= min_breed]
    parents = breedable[: max(2, int(len(breedable) * elite_frac))] if len(breedable) >= 2 else []
    n_cull = int(len(ranked) * cull_frac)
    parent_ids = {p.id for p in parents}
    culled = [r for r in ranked[len(ranked) - n_cull:] if r.id not in parent_ids] if n_cull else []
    return parents, culled


def day_cluster_evidence(day_of_episode: Sequence[Hashable], values: Sequence[float]) -> MeanEvidence:
    """Evidence over per-day means: one observation per UTC day."""
    by_day: dict[Hashable, list[float]] = {}
    for d, v in zip(day_of_episode, values):
        by_day.setdefault(d, []).append(float(v))
    return MeanEvidence.from_values(sum(v) / len(v) for v in by_day.values())


@dataclass(frozen=True, slots=True)
class PromotionCheck:
    ok: bool
    reason: str
    k: int  # episodes tested (the checkpoint)
    raw_mean: float
    day_lower: float
    n_days: int
    excess_post_mean: float
    excess_lower: float


def promotion_check(
    days: Sequence[Hashable],
    raw: Sequence[float],
    excess: Sequence[float],
    prior: EBPrior | None,
    *,
    min_fitness: float,
    conf: float = PROMOTE_CONF,
    min_days: int = PROMOTE_MIN_DAYS,
    checkpoints: Sequence[int] = PROMOTE_CHECKPOINTS,
) -> PromotionCheck:
    """Test one genome on its first K episodes (K = `checkpoint(len(raw))`).

    `days`/`raw` are per-episode UTC day and score in time order; `excess` the
    same episodes' `excess_values` (episodes without a contemporary are
    absent, so it is cut at the same share). `prior` is the excess prior.
    """
    k = checkpoint(len(raw), checkpoints)
    if k is None:
        return PromotionCheck(False, f"n={len(raw)} < {checkpoints[0]}", len(raw), 0.0, 0.0, 0, 0.0, 0.0)
    raw_ev = MeanEvidence.from_values(raw[:k])
    day_ev = day_cluster_evidence(days[:k], raw[:k])
    k_ex = round(len(excess) * k / len(raw))
    ex_ev = MeanEvidence.from_values(excess[:k_ex])
    pm, ps = posterior(ex_ev, prior)
    ex_lo = pm - _N.inv_cdf(conf) * ps
    day_lo = day_ev.lower(conf)
    common = (k, raw_ev.mean, day_lo, day_ev.n, pm, ex_lo)
    if day_ev.n < min_days:
        return PromotionCheck(False, f"{day_ev.n} day(s) < {min_days}", *common)
    if raw_ev.mean < min_fitness:
        return PromotionCheck(False, f"mean {raw_ev.mean:.4f} < {min_fitness}", *common)
    if day_lo <= 0:
        return PromotionCheck(False, f"day-clustered lower bound {day_lo:.4f} <= 0", *common)
    if ex_lo <= 0:
        return PromotionCheck(False, f"excess-over-population lower bound {ex_lo:.4f} <= 0", *common)
    return PromotionCheck(True, "ok", *common)

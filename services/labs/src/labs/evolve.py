"""Selection + reproduction.

Each evolution tick (rule: `labs.selection`, 2026-10-09):
    - Score every active genome on its episodes' excess over the rest of the
      population in the same hour, shrunk toward the population
      (empirical Bayes over the genomes scored in the last 7 days)
    - Rank genomes with >= MIN_EVAL_PER_GEN such episodes by posterior mean
    - Retire the worst CULL_FRAC of the ranked (status='retired')
    - Breed the free places from the top ELITE_FRAC of genomes with
      >= selection.MIN_BREED_EPISODES; with fewer than two of those, fill them
      with random immigrants instead

Population stays at TARGET_POPULATION between cycles.

Optional LLM commentary: rationale lines can be enriched (left for later).
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime
from loguru import logger
from sqlalchemy import desc, select

from matrix_shared import shared_session_scope
from matrix_shared.evidence import EBPrior, MeanEvidence
from matrix_shared.models import LabExperiment

from labs.evaluate import hour_bucket, population_episodes
from labs.genome import Genome, crossover, mutate, random_genome
from labs.selection import excess_values, fit_prior, plan_generation, rank

TARGET_POPULATION = 20
MIN_EVAL_PER_GEN = 5  # need >= this many scored evals to be ranked
ELITE_FRAC = 0.25  # top 25% survive as-is
CULL_FRAC = 0.40  # bottom 40% retired


@dataclass(slots=True)
class PopulationEvidence:
    raw: dict  # experiment id → [(generated_at, score)] per episode, time order
    excess: dict  # experiment id → [excess over contemporaries] per episode
    evidence: dict  # experiment id → MeanEvidence of its excess
    prior: EBPrior | None


async def population_evidence(asset_class: str) -> PopulationEvidence:
    """Episodes, excess and the empirical-Bayes prior of one asset class."""
    raw = await population_episodes(asset_class)
    excess = excess_values({eid: [(hour_bucket(ts), v) for ts, v in eps] for eid, eps in raw.items()})
    evidence = {eid: MeanEvidence.from_values(xs) for eid, xs in excess.items()}
    return PopulationEvidence(raw, excess, evidence, fit_prior(evidence.values()))


@dataclass(slots=True)
class GenerationReport:
    new_generation: int
    elites: int
    born: int
    retired: int


async def _next_generation_number(asset_class: str = "crypto") -> int:
    async with shared_session_scope() as session:
        stmt = (
            select(LabExperiment.generation)
            .where(LabExperiment.asset_class == asset_class)
            .order_by(desc(LabExperiment.generation))
            .limit(1)
        )
        row = (await session.execute(stmt)).first()
        return (row[0] if row else -1) + 1


async def _active_experiments(asset_class: str = "crypto") -> list[LabExperiment]:
    async with shared_session_scope() as session:
        stmt = (
            select(LabExperiment)
            .where(LabExperiment.status == "active")
            .where(LabExperiment.asset_class == asset_class)
        )
        return list((await session.execute(stmt)).scalars())


async def seed_initial_population(
    n: int = TARGET_POPULATION, asset_class: str = "crypto"
) -> int:
    """If there are no active experiments for this asset_class, seed N random genomes."""
    existing = await _active_experiments(asset_class)
    if existing:
        return 0
    rng = random.Random()
    for _ in range(n):
        g = random_genome(rng)
        async with shared_session_scope() as session:
            session.add(
                LabExperiment(
                    asset_class=asset_class,
                    generation=0,
                    params=g.to_dict(),
                    rationale=f"initial random seed [{asset_class}]",
                    status="active",
                )
            )
    logger.info(
        f"seeded initial population of {n} genomes [{asset_class}] at generation 0"
    )
    return n


async def run_evolution_cycle(
    *,
    rng: random.Random | None = None,
    min_eval_per_gen: int = MIN_EVAL_PER_GEN,
    asset_class: str = "crypto",
) -> GenerationReport:
    """One selection + reproduction cycle.

    Returns counts of elites/born/retired. If not enough ranked candidates,
    returns zeros (caller should call again later).
    """
    rng = rng or random.Random()
    actives = await _active_experiments(asset_class)
    if not actives:
        # If we got drained, reseed
        await seed_initial_population(asset_class=asset_class)
        return GenerationReport(new_generation=0, elites=0, born=TARGET_POPULATION, retired=0)

    pop = await population_evidence(asset_class)
    by_id = {e.id: e for e in actives}
    ranked = rank(
        {e.id: pop.evidence.get(e.id, MeanEvidence(0, 0.0, 0.0)) for e in actives},
        pop.prior, min_rank=min_eval_per_gen, salt=rng.getrandbits(32),
    )
    if len(ranked) < max(4, int(TARGET_POPULATION * ELITE_FRAC)):
        logger.info(
            f"evolution: only {len(ranked)} ranked candidates (need ~"
            f"{int(TARGET_POPULATION * ELITE_FRAC)}); waiting"
        )
        return GenerationReport(new_generation=-1, elites=0, born=0, retired=0)

    parents, culled = plan_generation(ranked, elite_frac=ELITE_FRAC, cull_frac=CULL_FRAC)
    elites = [by_id[p.id] for p in parents]
    elite_genomes = [Genome.from_dict(e.params) for e in elites]
    new_gen = await _next_generation_number(asset_class)

    retired_count = 0
    if culled:
        async with shared_session_scope() as session:
            for r in culled:
                e_db = await session.get(LabExperiment, r.id)
                if e_db is None:
                    continue
                e_db.status = "retired"
                e_db.retired_at = datetime.now(UTC)
                retired_count += 1

    # Refill to TARGET_POPULATION: offspring of two breedable elites, or a
    # random immigrant when fewer than two genomes have earned breeding.
    loser_ids = {r.id for r in culled}
    deficit = max(0, TARGET_POPULATION - sum(1 for e in actives if e.id not in loser_ids))
    born = 0
    for _ in range(deficit):
        if len(elites) >= 2:
            ia, ib = rng.sample(range(len(elites)), 2)
            child = mutate(crossover(elite_genomes[ia], elite_genomes[ib], rng), rng)
            parent_a, parent_b = elites[ia].id, elites[ib].id
            rationale = (
                f"gen {new_gen}: crossover of two of {len(elites)} breedable elites "
                f"(EB posterior excess), gaussian mutation"
            )
        else:
            child, parent_a, parent_b = random_genome(rng), None, None
            rationale = f"gen {new_gen}: random immigrant (fewer than two breedable genomes)"
        async with shared_session_scope() as session:
            session.add(
                LabExperiment(
                    asset_class=asset_class,
                    generation=new_gen,
                    parent_a_id=parent_a,
                    parent_b_id=parent_b,
                    params=child.to_dict(),
                    rationale=rationale,
                    status="active",
                )
            )
        born += 1

    n_elite = len(elites)
    logger.info(
        f"evolution gen={new_gen}: elites={n_elite} retired={retired_count} born={born}"
    )
    return GenerationReport(
        new_generation=new_gen, elites=n_elite, born=born, retired=retired_count
    )

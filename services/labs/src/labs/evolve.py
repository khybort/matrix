"""Selection + reproduction.

Each evolution tick:
    - Pick experiments with status='active' and n_evaluations >= MIN_EVAL_PER_GEN
    - Rank by fitness_score (highest = best)
    - ELITE_FRAC of top → carried over (already alive)
    - For the remaining target population size, breed offspring from elites via
      crossover + mutation
    - Retire the worst CULL_FRAC of active experiments (status='retired')

Population stays at TARGET_POPULATION between cycles.

Optional LLM commentary: rationale lines can be enriched (left for later).
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from loguru import logger
from sqlalchemy import desc, select

from matrix_shared import session_scope
from matrix_shared.models import LabExperiment

from labs.genome import Genome, crossover, mutate, random_genome

TARGET_POPULATION = 20
MIN_EVAL_PER_GEN = 5  # need >= this many scored evals to be ranked
ELITE_FRAC = 0.25  # top 25% survive as-is
CULL_FRAC = 0.40  # bottom 40% retired


@dataclass(slots=True)
class GenerationReport:
    new_generation: int
    elites: int
    born: int
    retired: int


async def _next_generation_number() -> int:
    async with session_scope() as session:
        stmt = select(LabExperiment.generation).order_by(desc(LabExperiment.generation)).limit(1)
        row = (await session.execute(stmt)).first()
        return (row[0] if row else -1) + 1


async def _active_experiments() -> list[LabExperiment]:
    async with session_scope() as session:
        stmt = select(LabExperiment).where(LabExperiment.status == "active")
        return list((await session.execute(stmt)).scalars())


async def seed_initial_population(n: int = TARGET_POPULATION) -> int:
    """If there are no active experiments at all, seed N random genomes at gen 0."""
    existing = await _active_experiments()
    if existing:
        return 0
    rng = random.Random()
    for _ in range(n):
        g = random_genome(rng)
        async with session_scope() as session:
            session.add(
                LabExperiment(
                    generation=0,
                    params=g.to_dict(),
                    rationale="initial random seed",
                    status="active",
                )
            )
    logger.info(f"seeded initial population of {n} genomes at generation 0")
    return n


async def run_evolution_cycle(
    *,
    rng: random.Random | None = None,
    min_eval_per_gen: int = MIN_EVAL_PER_GEN,
) -> GenerationReport:
    """One selection + reproduction cycle.

    Returns counts of elites/born/retired. If not enough ranked candidates,
    returns zeros (caller should call again later).
    """
    rng = rng or random.Random()
    actives = await _active_experiments()
    if not actives:
        # If we got drained, reseed
        await seed_initial_population()
        return GenerationReport(new_generation=0, elites=0, born=TARGET_POPULATION, retired=0)

    ranked = [e for e in actives if e.n_evaluations >= min_eval_per_gen]
    if len(ranked) < max(4, int(TARGET_POPULATION * ELITE_FRAC)):
        logger.info(
            f"evolution: only {len(ranked)} ranked candidates (need ~"
            f"{int(TARGET_POPULATION * ELITE_FRAC)}); waiting"
        )
        return GenerationReport(new_generation=-1, elites=0, born=0, retired=0)

    ranked.sort(key=lambda e: Decimal(e.fitness_score), reverse=True)

    n_elite = max(2, int(len(ranked) * ELITE_FRAC))
    elites = ranked[:n_elite]
    elite_genomes = [Genome.from_dict(e.params) for e in elites]
    new_gen = await _next_generation_number()

    # Retire bottom CULL_FRAC of ranked
    n_cull = int(len(ranked) * CULL_FRAC)
    losers = ranked[-n_cull:] if n_cull > 0 else []
    retired_count = 0
    if losers:
        loser_ids = [e.id for e in losers]
        async with session_scope() as session:
            for lid in loser_ids:
                e_db = await session.get(LabExperiment, lid)
                if e_db is None:
                    continue
                e_db.status = "retired"
                e_db.retired_at = datetime.now(UTC)
                retired_count += 1

    # Birth new offspring to bring population back to TARGET_POPULATION
    actives_after = [e for e in actives if e.id not in {l.id for l in losers}]
    deficit = max(0, TARGET_POPULATION - len(actives_after))
    born = 0
    if deficit > 0 and elite_genomes:
        for _ in range(deficit):
            a, b = rng.sample(elite_genomes, 2) if len(elite_genomes) >= 2 else (
                elite_genomes[0],
                elite_genomes[0],
            )
            child = mutate(crossover(a, b, rng), rng)
            rationale = (
                f"gen {new_gen}: crossover of two top-{n_elite} elites, "
                f"weights normalized, gaussian mutation"
            )
            async with session_scope() as session:
                # Find lineage IDs (find which experiments these elite_genomes came from)
                # Cheap approach: just record any two elite IDs for provenance
                parent_a = elites[rng.randrange(len(elites))].id
                parent_b = elites[rng.randrange(len(elites))].id
                session.add(
                    LabExperiment(
                        generation=new_gen,
                        parent_a_id=parent_a,
                        parent_b_id=parent_b,
                        params=child.to_dict(),
                        rationale=rationale,
                        status="active",
                    )
                )
            born += 1

    logger.info(
        f"evolution gen={new_gen}: elites={n_elite} retired={retired_count} born={born}"
    )
    return GenerationReport(
        new_generation=new_gen, elites=n_elite, born=born, retired=retired_count
    )

"""False-promotion simulation for the labs selection rule.

Runs the evolution + promotion loop on synthetic genomes and counts the
promotions a rule makes when NO genome has an edge (`null`), and its true and
false promotions when some do (`edge`). Compares the pre-2026-10-09 rule (raw
variance-penalised fitness; rank and breed at n >= 5; promote the best genome
at n >= 30 & fitness >= 0.05 on any scan) with `labs.selection`.

Calibrated on the lab's own episodes (Sep 2026, 13 869 episodes): per-episode
score sd 0.66 = population shock (0.33 per hour, 0.16 per day with day-to-day
autocorrelation 0.5 — the genomes trade the same symbols in the same hours)
+ idiosyncratic 0.50; ~1 episode per genome-hour; evolution every 5 minutes,
promotion scan every 10. `--iid` drops the shared shock.

    uv run python -m labs.zero_edge_sim --reps 200 --days 30
    uv run python -m labs.zero_edge_sim --scenario edge
"""

from __future__ import annotations

import argparse
import math
import random
from dataclasses import dataclass, field

from matrix_shared.evidence import MeanEvidence

from labs.selection import (
    MIN_BREED_EPISODES,
    PROMOTE_CONF,
    PROMOTE_MIN_DAYS,
    excess_from_totals,
    fit_prior,
    plan_generation,
    promotion_check,
    rank,
)

CYCLES_PER_HOUR = 12
CYCLES_PER_DAY = 288  # 5-minute evolution cycles
SCAN_EVERY = 2  # promotion scan every 10 minutes
TARGET_POPULATION = 20
ELITE_FRAC = 0.25
CULL_FRAC = 0.40
MIN_RANK = 5
PROMOTE_MIN_N = 30
PROMOTE_MIN_FITNESS = 0.05
PRIOR_WINDOW_CYCLES = 7 * CYCLES_PER_DAY


@dataclass
class Genome:
    gid: int
    mu: float
    eps: list[tuple[int, float]] = field(default_factory=list)  # (hour bucket, score)

    def raw(self) -> MeanEvidence:
        return MeanEvidence.from_values(v for _, v in self.eps)


def old_fitness(ev: MeanEvidence, k: float = 1.0) -> float:
    """labs.evaluate.compute_fitness in floats (the pre-2026-10-09 rank key)."""
    if ev.n <= 0:
        return 0.0
    pen = k * ev.sd / math.sqrt(ev.n) if ev.n > 1 else 0.0
    return (ev.mean - pen) * math.sqrt(min(ev.n, 25) / 25)


@dataclass
class Result:
    false_promotions: int = 0  # true mu <= 0
    true_promotions: int = 0  # true mu > 0
    mu_start: float = 0.0
    mu_end: float = 0.0
    births: int = 0


def simulate(
    rule: str,
    *,
    days: int,
    rng: random.Random,
    scenario: str = "null",
    iid: bool = False,
    lam: float = 1.0 / CYCLES_PER_HOUR,
    min_breed: int = MIN_BREED_EPISODES,
    conf: float = PROMOTE_CONF,
    min_days: int = PROMOTE_MIN_DAYS,
) -> Result:
    hour_sd, day_sd, day_rho, idio_sd = (0.0, 0.0, 0.0, 0.66) if iid else (0.33, 0.16, 0.5, 0.50)
    next_id = 0

    def new_genome(parents: tuple[Genome, Genome] | None = None) -> Genome:
        nonlocal next_id
        next_id += 1
        if scenario == "null":
            mu = 0.0
        elif parents is None:  # edge: seed spread around a losing population
            mu = rng.gauss(-0.05, 0.08)
        else:  # heritable, with mutation noise
            mu = (parents[0].mu + parents[1].mu) / 2 + rng.gauss(0, 0.03)
        return Genome(next_id, mu)

    actives = [new_genome() for _ in range(TARGET_POPULATION)]
    retired: list[tuple[int, MeanEvidence]] = []  # (retired at, excess evidence)
    tot: dict[int, float] = {}
    cnt: dict[int, int] = {}
    res = Result(mu_start=sum(g.mu for g in actives) / len(actives))
    day_shock = hour_shock = 0.0
    prior = None
    ex_cache: dict[int, MeanEvidence] = {}

    def excess(g: Genome) -> list[float]:
        return excess_from_totals(g.eps, tot, cnt)

    def retire(t: int, g: Genome) -> None:
        retired.append((t, MeanEvidence.from_values(excess(g))))

    for t in range(days * CYCLES_PER_DAY):
        hour = t // CYCLES_PER_HOUR
        if t % CYCLES_PER_DAY == 0:
            day_shock = day_rho * day_shock + math.sqrt(1 - day_rho**2) * rng.gauss(0, day_sd)
        if t % CYCLES_PER_HOUR == 0:
            hour_shock = rng.gauss(0, hour_sd)
        fresh: set[int] = set()
        for g in actives:
            if rng.random() < lam:
                v = g.mu + day_shock + hour_shock + rng.gauss(0, idio_sd)
                g.eps.append((hour, v))
                tot[hour] = tot.get(hour, 0.0) + v
                cnt[hour] = cnt.get(hour, 0) + 1
                fresh.add(g.gid)

        by_id = {g.gid: g for g in actives}
        if rule == "new":
            # Excess evidence is refreshed for genomes with a new episode and
            # for everyone on the hour (a later episode in the same bucket
            # moves the others' excess slightly) — a speed-up, not a rule.
            hourly = t % CYCLES_PER_HOUR == 0
            for g in actives:
                if hourly or g.gid in fresh or g.gid not in ex_cache:
                    ex_cache[g.gid] = MeanEvidence.from_values(excess(g))
            members = {g.gid: ex_cache[g.gid] for g in actives}
            if hourly:
                retired = [(rt, ev) for rt, ev in retired if t - rt <= PRIOR_WINDOW_CYCLES]
                prior = fit_prior([ev for _, ev in retired] + list(members.values()))

        # --- evolution ---
        if rule == "old":
            ranked_old = sorted(
                (g for g in actives if len(g.eps) >= MIN_RANK),
                key=lambda g: old_fitness(g.raw()), reverse=True,
            )
            if len(ranked_old) >= max(4, int(TARGET_POPULATION * ELITE_FRAC)):
                elites = ranked_old[: max(2, int(len(ranked_old) * ELITE_FRAC))]
                n_cull = int(len(ranked_old) * CULL_FRAC)
                losers = {g.gid for g in ranked_old[-n_cull:]} if n_cull else set()
                for gid in losers:
                    retire(t, by_id[gid])
                actives = [g for g in actives if g.gid not in losers]
                while len(actives) < TARGET_POPULATION:
                    actives.append(new_genome(tuple(rng.sample(elites, 2))))
                    res.births += 1
        else:
            ranked = rank(members, prior, salt=t)
            if len(ranked) >= max(4, int(TARGET_POPULATION * ELITE_FRAC)):
                parents, culled = plan_generation(
                    ranked, elite_frac=ELITE_FRAC, cull_frac=CULL_FRAC, min_breed=min_breed
                )
                losers = {r.id for r in culled}
                for gid in losers:
                    retire(t, by_id[gid])
                actives = [g for g in actives if g.gid not in losers]
                pg = [by_id[p.id] for p in parents]
                while len(actives) < TARGET_POPULATION:
                    # fewer than two breedable parents → a random immigrant
                    actives.append(new_genome(tuple(rng.sample(pg, 2)) if len(pg) >= 2 else None))
                    res.births += 1

        # --- promotion scan ---
        if t % SCAN_EVERY:
            continue
        promoted: Genome | None = None
        if rule == "old":
            cands = [g for g in actives if len(g.eps) >= PROMOTE_MIN_N]
            if cands:
                best = max(cands, key=lambda g: old_fitness(g.raw()))
                if old_fitness(best.raw()) >= PROMOTE_MIN_FITNESS:
                    promoted = best
        else:
            for g in sorted(
                (g for g in actives if len(g.eps) >= PROMOTE_MIN_N),
                key=lambda g: len(g.eps), reverse=True,
            ):
                chk = promotion_check(
                    [b // 24 for b, _ in g.eps], [v for _, v in g.eps], excess(g), prior,
                    min_fitness=PROMOTE_MIN_FITNESS, conf=conf, min_days=min_days,
                )
                if chk.ok:
                    promoted = g
                    break
        if promoted is not None:
            if promoted.mu > 0:
                res.true_promotions += 1
            else:
                res.false_promotions += 1
            # status 'promoted' leaves the population; a fresh genome takes the place
            retire(t, promoted)
            actives = [g for g in actives if g.gid != promoted.gid] + [new_genome()]

    res.mu_end = sum(g.mu for g in actives) / len(actives)
    return res


def run(
    reps: int, days: int, scenario: str = "null", *, iid: bool = False, seed: int = 1,
    rules: tuple[str, ...] = ("old", "new"), **kw,
) -> dict[str, dict[str, float]]:
    out = {}
    for rule in rules:
        rs = [simulate(rule, days=days, rng=random.Random(seed + i), scenario=scenario, iid=iid, **kw)
              for i in range(reps)]
        out[rule] = {
            "p_any_false": sum(r.false_promotions > 0 for r in rs) / reps,
            "false_per_30d": sum(r.false_promotions for r in rs) / reps * 30 / days,
            "true_per_30d": sum(r.true_promotions for r in rs) / reps * 30 / days,
            "mu_gain": sum(r.mu_end - r.mu_start for r in rs) / reps,
            "births_per_day": sum(r.births for r in rs) / reps / days,
        }
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--reps", type=int, default=200)
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--scenario", choices=("null", "edge"), default="null")
    ap.add_argument("--iid", action="store_true", help="no shared market shock")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--rules", default="old,new")
    ap.add_argument("--min-breed", type=int, default=MIN_BREED_EPISODES)
    ap.add_argument("--conf", type=float, default=PROMOTE_CONF)
    ap.add_argument("--min-days", type=int, default=PROMOTE_MIN_DAYS)
    a = ap.parse_args()
    res = run(a.reps, a.days, a.scenario, iid=a.iid, seed=a.seed, rules=tuple(a.rules.split(",")),
              min_breed=a.min_breed, conf=a.conf, min_days=a.min_days)
    print(f"scenario={a.scenario} iid={a.iid} reps={a.reps} days={a.days} "
          f"min_breed={a.min_breed} conf={a.conf} min_days={a.min_days}")
    for rule, r in res.items():
        print(
            f"  {rule:>3}: P(>=1 false promotion)={r['p_any_false']:.3f}  "
            f"false/30d={r['false_per_30d']:.2f}  true/30d={r['true_per_30d']:.2f}  "
            f"mean-mu gain={r['mu_gain']:+.4f}  births/day={r['births_per_day']:.1f}"
        )


if __name__ == "__main__":
    main()

---
title: Learning-loop statistics — selecting on evidence, not on noise
updated: 2026-10-09
sources: [services/labs/src/labs/selection.py, services/labs/src/labs/zero_edge_sim.py, packages/python-shared/src/matrix_shared/evidence.py, services/reflection/src/reflection/mutate.py, services/reflection/src/reflection/efficacy.py, services/agent/src/agent/decide.py, "db: lab_evaluations scored 2026-06-01…10-09 (75 020 rows → 13 869 episodes, 1 157 genomes)", "db: predictions ⋈ outcomes since 2026-09-01, 24 h strategy-days", "db: matrix_agent ε-probe trace 2026-10-09 (predictions.context is_exploration ⋈ outcomes, agent_lessons, slot scorer replay, edge-study replay)"]
status: current
---

Three automatic selectors in the [[learning-loop]] acted on raw means of a
handful of episodes: labs evolution (rank and breed at n ≥ 5), reflection's
mutation trigger (n ≥ 10 and a negative total), and the challenger cutover
(z ≥ 1 checked every tick). On 2026-10-09 each was measured against a
zero-edge null and replaced with a rule whose false-positive rate is known.
The shared tools are in `matrix_shared/evidence.py`: Student-t bounds of a
per-episode mean and normal–normal empirical Bayes (DerSimonian–Laird τ²,
pooled σ²).

## 1. Labs evolution and promotion

### What the lab data says (13 869 episodes, 1 157 genomes)
- Per-episode score sd **0.66**; the median genome lived **1.3 h** and was
  ranked on 7 episodes. A five-episode mean has a standard error of ~0.29,
  larger than any edge this system has measured.
- **The genomes share a market shock.** The population-mean score moves with
  sd **0.38 between hours and 0.18 between days** (Sep 2026): genomes built
  from the same five features trade the same symbols in the same hours.
  Thirty episodes from one afternoon are closer to one observation than to
  thirty, and a raw mean ranks *when* a genome was alive.
- **Once the same-hour market is removed, genomes do not differ.** Excess
  over contemporaries (each episode minus the mean of the other genomes'
  episodes in the same UTC hour), pooled over all 1 001 genomes with ≥ 5
  episodes: DerSimonian–Laird **τ² = 0** (μ₀ = +0.002, σ² = 0.357). There is
  no between-genome dispersion beyond noise; 496 generations of breeding
  selected on luck.
- On episodes, none of the 13 `lab_promotion` proposals (06-01 … 09-14) had
  passed the old bar when it was made: 12 had < 30 episodes, the 13th
  (45c837ca, 49 episodes) had fitness −0.011. No genome in history passes the
  old bar or the new rule on its full record (109 reached 30 episodes).

### The rule since 2026-10-09 (`labs.selection`)
- **Rank** on the empirical-Bayes posterior mean of *excess* over
  contemporaries, the prior fitted over the genomes scored in the last 7 days
  (`labs.evolve.population_evidence`). With τ² = 0 every posterior is μ₀ and
  ties break at random (a fresh salt every cycle).
- **Cull** the bottom 40 % of genomes with ≥ 5 such episodes (unchanged bar:
  retiring on noise only wastes a slot).
- **Breed** only from genomes with ≥ `MATRIX_LAB_MIN_BREED_EPISODES` (20);
  fewer than two → the free places go to random immigrants.
- **Promote** on pre-registered looks (first 30, 60, 120, 240 episodes; a
  look is a fixed prefix, so the 10-minute scan is not a thousand tests) only
  if all hold: episodes on ≥ 3 UTC days; raw mean ≥ min_fitness (0.05);
  day-clustered one-sided 95 % lower bound > 0; posterior excess one-sided
  95 % lower bound > 0. `metrics_window` records every number; `fitness_score`
  there is the tested mean, so the auto-apply bar reads the same figure.
- The stored `fitness_score` column is still the old raw key (display only);
  the leaderboard prints `ex_n` / `ex_post` beside it.

### Applied to the live leaderboard (2026-10-09 16:30 UTC)
20 active crypto genomes (gen 434–496), none with ≥ 20 episodes, four with
≥ 5 excess episodes (evolution waits for five). 7-day prior: 38 genomes,
τ² = 0, μ₀ = −0.004 — every ranked genome's posterior is μ₀ ± 0.017. The old
rule would make 5a3c92bd (fitness −0.035) and 010ef9d4 (−0.044) the parents
of every child and cull 4d64081a and 014e9f4d on 6–7 episodes; the new rule
breeds nobody (no genome has earned it — immigrants fill the places) and culls
at random among equals. No genome is near promotion under either rule.

### Zero-edge simulation (`python -m labs.zero_edge_sim`)
Same code as live (`labs.selection`); calibrated as above (idiosyncratic sd
0.50 + hour shock 0.33 + day shock 0.16 with day-to-day ρ 0.5; one episode
per genome-hour; evolution every 5 min, scan every 10 min); 30 days.

| scenario | rule | P(≥ 1 false promotion) | false / 30 d | true / 30 d | population μ gain |
|---|---|---|---|---|---|
| null, shared shock (200 runs) | old | **1.00** | **18.3** | — | — |
| null, shared shock (200 runs) | new | **0.025** | **0.04** | — | — |
| null, iid (200 runs) | old | 1.00 | 18.6 | — | — |
| null, iid (200 runs) | new | 0.06 | 0.06 | — | — |
| edge (100 runs) | old | 0.43 | 1.24 | 38.2 | +0.47 |
| edge (100 runs) | new | 0.00 | 0.00 | 0.36 | +0.12 |
| edge, breed gate 10 (100 runs) | new | 0.01 | 0.01 | 0.01 | +0.10 |

`edge`: seed genomes μ ~ N(−0.05, 0.08), children inherit the parents' mean
+ N(0, 0.03). The old rule climbs faster *in that world* because it breeds
young genomes on short generations; the measured world has τ² = 0, where
its speed buys nothing and its false promotions are certain. Intermediate
variants on the way (null, 40–100 reps): EB on raw scores with every-scan
testing 8.9 false/30 d, with pre-registered looks 3.7 — the market shock,
not the n ≥ 5 rank alone, was what promoted noise. Breaking posterior ties
(τ² = 0) on n·(mean − μ₀) instead of at random doubled false promotions
(0.09 → 0.18): it is selection on luck by another name. A breeding gate of 10
was no faster than 20 in the edge world, so 20 stays.

## 2. ε-exploration: what a probe buys
Traced 2026-10-09 over all matrix_agent probes (`context.is_exploration`):

| consumer | did probes change a decision? |
|---|---|
| lessons (`agent_lessons`) | **no** — 0 of 27 stored lessons, 0 of 2 340 rolling (symbol, side) windows cross the 0.4 gate differently; ≤ 3 probe episodes per bucket per week against a 20-episode gate; the bypass corridor fired **0 times ever** |
| setup memory | no — 0 of 168 verdicts |
| edge study / promotion / certificate | no verdict change (all `unproven`), but probes dilute the evidence: crypto 40 d gross +0.9 → +5.0 bps without them; us edge t −1.87 → −0.38 (near a `harmful` verdict mostly on probes) |
| slot scorer | yes, as contamination — 131 of 1 545 replayed close events branch differently (52 crypto zero-demotions caused by probes); bist was scored on probes alone |
| reflection `_underperforming` | yes — 2 of 12 matrix_agent strategy-days: us 09-15 (21 episodes, 8 the policy's) and bist 09-16 (19, 0 the policy's) produced the us `threshold_change` and bist `weight_tune` |

Cost: 120 filled probe episodes all time, **−22.2 bps net per episode
(t = −2.8), −$3.77**; September −$3.22 for probes vs +$2.56 for the policy's
own fills; probes held 60 % of matrix_agent's position-hours in September.

**Budget since 2026-10-09** (`agent.decide.explore_epsilon_for`): a probe can
change a decision only where an active, confident (≥ 0.4), non-operator
`avoid` lesson covers its (symbol, side) — the corridor that can retire the
lesson. There ε × `MATRIX_EXPLORE_CORRIDOR_MULT` (3); everywhere else
ε × `MATRIX_EXPLORE_MAINTENANCE_SHARE` (0.33) × the symbol-edge scale, which
keeps a same-hours control arm alive for offline studies
([[llm-value-audit]] used it) without zeroing exploration. Probes carry
`explore_cell = corridor | maintenance`. With 0 active lessons today every
probe is maintenance: **~310 → ~105 probe signal-episodes a week
(≈ −3 400 → −1 150 bps·episodes), ~43 → ~14 filled probe episodes a week
(≈ −$1.6 → −$0.5)**. Paper-only, never zero.

Probes are now excluded from reflection's metrics and efficacy samples
(`reflection.metrics.is_probe`). Still counted, as of 2026-10-09: the slot
scorer (arguably right — probes do occupy the strategy's slots) and
`edge_study._load_candidates` (shared module, not changed here; filtering
`is_exploration` there as `is_shadow` already is would remove the dilution).

## 3. Reflection's mutation trigger
Old: n ≥ 10 episodes and (total < 0 or avg score < trigger; deployed trigger
−0.20). A strategy with zero edge crosses that **~50 %** of the time
(20 000 trials at n = 10/20/50: 0.50, also with fat tails). New
(`mutate._underperforming`): n ≥ 10 and the **one-sided 95 % Student-t upper
bound of realised net USD per episode < 0**, probes excluded → **4.8–5.0 %**
under the same null. `MATRIX_MUTATION_CONF` sets the confidence.

Real strategy-days since 09-01 (24 h, champions, probes excluded under the
new gate): 109 strategy-days, 42 with ≥ 10 episodes; the deployed gate
flagged **35**, the new one **12**; 23 change (all old-yes → new-no: grid
×7 versions on 09-12/13, oi_delta, dca, cash_and_carry, momentum_xs 09-13/14/21,
matrix_agent crypto/us/bist). Of the 43 rule/LLM mutation proposals made
since 09-01, **7** fall on a day the new gate also flags. Momentum_xs on
09-21 (−$54.9 over 90 episodes, mean −0.61, upper +0.03) is the closest
miss: a big loss carried by a few episodes is not yet a significant one.

The 10-minute tick re-tests a sliding 24 h window, so the per-look 5 % is
not the per-day rate; the windows overlap almost entirely, the old gate had
the same property, and cooldown/blocked-by-challenger limit how often a flag
becomes a proposal.

## 4. Efficacy, made consistent
Same sample as the trigger: per-episode summed `pnl_usd`
(`edge_study.episode_pnls`), sample sd (n − 1), `orphan_flat_close` and now
probes excluded. Thresholds:
- before/after rollback: one final look at n ≥ 50, `z ≤ −1` and total < 0
  (unchanged; reverting to a config the system already ran is the cheap
  direction).
- **challenger cutover: `Z_POS` 1.0 → 1.645.** The check repeats every tick
  until a verdict; simulated with equal true means it cut a no-edge challenger
  over **34 %** of the time at 1.0 and **17 %** at 1.645, while a +0.2 sd edge
  still cuts over 75 % (85 % at 1.0). A change now needs the same one-sided
  95 % to happen as a mutation needs to be proposed.

## Open
- A real heritable edge (τ² > 0 on excess) has never been measured in the
  lab; if one appears, the breeding gate (20) is what slows its spread —
  revisit with the `edge` scenario before lowering it.
- Lessons can never reach their gate from probes at current volume; the
  corridor only matters once an `avoid` lesson fires.

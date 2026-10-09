---
title: Matrix wiki index
updated: 2026-10-09
status: current
---

Compiled knowledge about this repository. Maintained by the LLM under the
`llm-wiki` skill: pages are updated in place, numbers carry dates, claims cite
their source. Machine- and operator-level knowledge lives in the global wiki
(`~/.claude/wiki/`).

## Current state (2026-10-09)

**What makes money: nothing proven.** No strategy in the book beats random
entry after cost once each bet counts once, entries start after the signal and
no window crosses a bar gap ([edge-study](edge-study.md),
[strategy-scoreboard](strategy-scoreboard.md)). Paper equity at the 10-09 day
start: crypto default −$526, crypto shadow −$159, BIST −$91, US −$1, each of
$10 000. The one candidate is **`neg_funding_carry`**, which is shadow-only:
long perp, short borrowed spot for 48 h after a settlement at or below −0.08 %
([signal-research-2026-10](signal-research-2026-10.md)). Its 1-year backtest
showed +135 bps per episode on the holdout. The adversarial check cut that to
about +20 bps at $500 a leg with realistic borrow, and its confidence interval
includes zero. Its verdict arrives in two steps:
1. The shadow tracker reaches a verdict at 20 closed episodes (48 h holds, 3
   open now). It reports `below_band` if the mean is ≤ +30 bps and `on_track`
   inside +100…+300.
2. At 30 `series`-borrow episodes, notify sends `review_due` once with the
   three borrow and decay revisit rules. Capital needs `confirmed` through
   `carry_edge_rows`: ≥ 20 day clusters, the pre-registered n (floor 200), and
   BHY plus deflated Sharpe ([operations](operations.md) "Shadow tracker").

**Rejected, and where it is recorded.** 91 pre-registered cells, so the
cumulative m is 91: round 1 has 38, round 2 (ticks) 29, round 3 (the
positive-funding mirror) 18, round 3b (the perp-perp hedge) 6. Only round 1's H1
survived. Round 4 (dated-futures basis, H13) was pre-registered in 17edd54.
Its results go in their own section of
[signal-research-2026-10](signal-research-2026-10.md), and its cells add to m.
Maker execution adds +4…+6 bps but turns nothing positive
([maker-execution](maker-execution.md)). The EV ranker cannot discriminate
(Spearman −0.06). The LLM and graph extraction earn nothing measurable
([llm-value-audit](llm-value-audit.md)). Withdrawn claims stay in the pages,
struck through, with the date and the commit that withdrew them:
- momentum_xs's +36 bps: pseudo-replication, withdrawn in 5475933 and 7fa47cd
- bist_volume_breakout's +104/+116 and matrix_agent/crypto `confirmed`: they
  scored across bar gaps, withdrawn in 2a72463
- grid "reliably worse than chance": an entry-rule artefact, withdrawn in cda6ee6

**Paused or retired, and how to revert.**
- BIST and US are `paused`: 6 `strategy_configs` rows, and ingestion runs
  `--markets crypto` (9091f38). To revert, set them back to `active` and add the
  markets ([market-cadence-study](market-cadence-study.md)).
- `cash_and_carry` and `xexch_funding_arb` are `retired` (2026-10-09). `grid`
  (09-20) and the momentum_xs champion (09-21) are retired too. Revive one only
  with new evidence, by setting its row back to `active`
  ([strategy-scoreboard](strategy-scoreboard.md)).
- The agent runs `rule_only` every 60 s (6a0884e, 9091f38), and graph
  extraction is `heuristic` with backfill off. To revert, set
  `MATRIX_LLM_BLEND_MODE=blend`, `GRAPH_EXTRACT_MODE=llm` and `--interval 15`.

**Operator-blocked** ([open-questions](open-questions.md)):
1. Keep the laptop on AC.
2. Enable automatic login. FileVault is off, so a power loss currently waits at
   the login window.
3. `ALTER SYSTEM` for `shared_buffers` (still 128 MB; needs a Postgres
   restart) and `autovacuum_work_mem` (headroom only at today's 5 GB heap).
4. `REINDEX INDEX CONCURRENTLY` on the four `market_trades` indexes: 54 GiB for
   a 5.2 GB heap.

**A new session checks first:**
1. The shadow tracker digest line, from the Director brief or from
   `shadow_tracker.collect()` + `format_line` in any container. At 16:55 UTC it
   read `COLLECTING — 0/20 closed ep, 3 open … 9 qualifying settlements/72h`.
2. `make test-all`. All 15 Python suites and the web typecheck were green on
   10-09.
3. `git status`. Several agents share this tree, so never `git add -A`
   ([development](development.md)).

Background: [purpose-and-goals](purpose-and-goals.md), then
[pnl-reality](pnl-reality.md).

## What it is
- [purpose-and-goals](purpose-and-goals.md) — the one goal, the success gates, where they stand
- [architecture](architecture.md) — shape of the system, data flow, two DB tiers
- [services](services.md) — every container, what it does, its cadence
- [data-model](data-model.md) — the tables that matter and which tier owns them

## What it does
- [strategies](strategies.md) — the signal catalogue and how a config becomes trades
- [paper-engine](paper-engine.md) — how a prediction becomes a position and an outcome
- [pnl-reality](pnl-reality.md) — measured economics: why it loses money
- [edge-study](edge-study.md) — controlled test of entry timing vs random entry (honest entry rule: first bar open after the signal); carries judged on realised paper episodes
- [strategy-scoreboard](strategy-scoreboard.md) — every emitting strategy on clean signals, net of cost (2026-10-09: none pays)
- [signal-research-2026-10](signal-research-2026-10.md) — rounds 1–3b (m = 91 cells) on 1y Bybit history and ticks; only hedged negative-funding carry (H1) survives, much smaller after the adversarial check; round 4 (dated-futures basis) pre-registered
- [market-cadence-study](market-cadence-study.md) — BIST/US vs their cost (pause both) and 15 s vs 60 s vs 300 s decisions (speed earns nothing)
- [maker-execution](maker-execution.md) — resting entries/exits on Bybit ticks: +4…+6 bps per episode, but no strategy turns net positive (2026-10-09)
- [llm-value-audit](llm-value-audit.md) — does the LLM or the knowledge graph earn its rate budget? (2026-10-09: neither; agent rule-only, graph heuristic)
- [methods](methods.md) — quant methodology applied, with why
- [research-backlog](research-backlog.md) — methods not yet applied, each with its decisive test
- [learning-loop](learning-loop.md) — reflection, labs, lessons, memory, and their gates
- [learning-loop-statistics](learning-loop-statistics.md) — every selector vs a zero-edge null: labs shrinkage + excess ranking, mutation CI gate, challenger z, what ε-probes buy (2026-10-09)
- [risk-gates](risk-gates.md) — what stands between the code and real capital

## How it runs
- [operations](operations.md) — commands, environment knobs, daily checks, streamed vs traded crypto universe (carry watchlist)
- [llm-stack](llm-stack.md) — model access, cost accounting, rate budget
- [development](development.md) — tests, hot reload, staging rules in a shared tree
- [incidents](incidents.md) — what has gone wrong, root causes, fixes
- [open-questions](open-questions.md) — what is unknown and what would settle it

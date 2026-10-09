---
title: LLM value audit — does the LLM or the knowledge graph earn its rate budget?
updated: 2026-10-09
sources: ["signal replay 2026-10-09 (matrix_shared.edge_study: one_per_episode, entry_index, simulate_bracket, welch; 10 random-time draws per signal; net of 2 × execution_cost_bps)", "db: predictions.context method / llm_side / total / scores / features.graph, matrix_agent non-shadow since 2026-09-01", "/root/.claude/matrix_usage/*.jsonl (LLM ledger)", "pg_locks advisory sampling of the MATRIX_LLM_GLOBAL_SLOTS locks, 2 s cadence, 10 min before/after", "strategy_configs matrix_agent/crypto v12 weights"]
status: current
---

Two consumers took almost all of the subscription's rate budget when it
worked: the decision agent (~1 382 calls a weekday) and graph extraction
(~5 900), see [[market-cadence-study]]. This page asks whether either buys
any PnL, on the same clean-signal method as [[strategy-scoreboard]].

## How the agent decides, and how to tell the arms apart
`agent.decide.decide_batch` always computes the rule score (weighted
trade_flow, funding, oi_delta, ob_imbalance, news; crypto v12 threshold 0.48),
then — when an LLM backend answers — blends it with the LLM's verdict
(`blend_decisions`): agree → trade (`llm+rule`), LLM-only → trade at 0.6×
confidence (`llm`), rule-only → trade at 0.6× (`rule`, with `llm_side` in the
context), opposite → hold. Holds may become ε-exploration probes in the rule's
sub-threshold lean (`…+explore`). `predictions.context.llm_side` is present
exactly when the LLM answered, so every signal falls in one arm:

| arm | meaning |
|---|---|
| LLM-directed | `llm` (LLM-only) and `llm+rule` (agree), not exploration |
| rule, LLM held | rule traded, LLM answered hold |
| rule, LLM down | rule traded, no LLM answer (outage 09-12, 09-22/23, 10-01…) |
| explore, LLM up/down | ε-probe in the rule's lean after a hold |

The 09-30 → 10-08 outage is mostly unusable (crypto bars missing 10-01…10-08);
the entry-age and bar-gap guards drop those signals automatically
(`unscorable` below). Rule-only evidence with fresh bars comes from 09-12/13
and 09-22/23.

## 1. Does the LLM earn its cost? — No (2026-10-09)

Crypto, one per episode, gross and net bps per episode, 09-12 … 10-09:

| arm | n ep | gross | net | t(net) | vs random time | t | fills, realised net |
|---|---|---|---|---|---|---|---|
| LLM-directed (pooled) | 251 | +11.7 | **−0.9** | −0.16 | +12.6 | 2.00 | 58 |
| ↳ LLM agree with rule | 21 | +42.3 | +28.6 | 0.95 | +60.0 | 1.94 | 14, −3.6 |
| ↳ LLM-only (rule held) | 230 | +8.9 | −3.6 | −0.61 | +8.2 | 1.31 | 44, −4.5 |
| rule, LLM held | 4 | +59.3 | +44.3 | 0.52 | +65.7 | 0.74 | 1 |
| rule, LLM down | 383 | +0.9 | −13.5 | −2.26 | +0.2 | 0.03 | 42, −31.6 |
| explore, LLM up | 145 | +13.9 | +1.1 | 0.12 | +13.3 | 1.36 | 21, −14.8 |
| explore, LLM down | 1 098 | +0.4 | −12.6 | −6.36 | +0.6 | 0.27 | 32, −3.1 |

US (paused since 10-09): LLM agree n=15 net +1.9 (t=0.19); rule −11.9
(t=−2.76, n=71); explore −11.7 (t=−4.96, n=223). BIST: only exploration,
−47 net.

The LLM arm looks better than the rule arm (−0.9 vs −13.5 net), but the two
were measured in different weeks. **The same-hours controls say the
difference is the regime, not the LLM:**

- **Direction.** For each LLM-directed episode, replay the same entry and
  bracket on the side of the rule's own lean (`context.total`): LLM side minus
  rule-lean side **−2.4 bps, t=−0.40, n=250**. When the LLM went against the
  lean (71 episodes) it made −6.1 gross; with the lean (159) +15.7.
- **When to trade.** In the hours the LLM was answering, the ε-probes it
  *declined* (both held, probe in the lean) did +13.9 gross; the trades it
  *took* +11.7: **−2.1 bps, t=−0.19** (251 vs 145).
- Day-clustered, LLM-directed gross is +20.3 (t=2.19, 14 days), but the
  declined probes of those same days carry the same lead over random; the
  09-13…09-30 regime favoured the rule's lean, whoever pulled the trigger.
  Five symbols (ENA, NEAR, RARE, QNT, BR) carry 3 139 bps of gross, more than the arm's whole 2 965.

**Verdict:** the LLM adds no measurable selection or direction skill over the
rule score it is shown, its trades net ≈ 0 after cost, and the gross level
(+11.7) is below the round trip (~12.6), so by [[edge-study]]'s `pays`
criterion it does not pay. Its cost is ~1 382 calls a weekday, p50 18.6 s per
call (which also stretched the 15 s loop to a 39 s median), and a share of the
same subscription rate budget every other LLM consumer uses.

## 2. Does the knowledge graph feed anything that makes money? — No

Consumers of graph output (grep of `graph.read` / `graph_signals` / AGE):

| consumer | what it reads | trades? |
|---|---|---|
| agent `features.py` → `rule_decide._news_score` | `graph_signals` mentions, direct/contextual polarity, recency (weight `news` 0.25 of 1.0 in crypto v12) | yes, through the rule score |
| agent `_llm_prompt` | same, plus related companies / co-mentioned assets | yes, through the LLM (now off) |
| synthesis | graph coverage, writes Concept/IMPACTS themes hourly | no (bulletin, deferred product) |
| brain tools | ad-hoc graph queries for chat | no |
| backtest `replayers/matrix_agent` | historical features | replay only |
| agent `_mirror_prediction_to_graph` | writes Prediction nodes | no (index) |

No strategy-service module and no paper-engine path reads the graph.

Measured on the agent's crypto episodes (net bps):

| arm | graph coverage | no coverage |
|---|---|---|
| LLM-directed | −1.4 (n=53) | −0.8 (n=198) |
| rule | −12.3 (n=27) | −13.9 (n=377) |
| explore | −14.3 (n=162) | −10.5 (n=1 081) |

- Coverage is thin: the news term was non-zero on ~110 of 2 726 agent
  predictions since 09-01, and its mean absolute contribution (≤0.03) is a
  fraction of the score (0.07–0.17).
- Episodes with a non-zero news term: **−16.3 net, t=−2.47, n=80**.
- Where the news term flipped the sign of the lean (the only place it changed
  a direction), actual side minus opposite side: **+5.0, t=0.14, n=14**.

**Verdict:** no traded decision's return depends measurably on the graph, and
where it touched a decision the result was no better. LLM extraction (agent
tool loop, then single-shot fallback) is pure cost; the keyword extractor keeps
asset mentions and title polarity alive for the rule's news term at zero LLM
spend.

## What changed (2026-10-09)

- `agent`: `MATRIX_LLM_BLEND_MODE=rule_only` in `docker-compose.yml`.
  `rule_only` previously still *made* the LLM call and discarded the answer;
  since 782b037 it skips it (`decide._ask_llm`). Effect on emitted trades:
  agree trades stay (the rule trades anyway, now at the rule's confidence),
  LLM-only trades (−3.6 net, n=230) disappear, the few conflicts become rule
  trades. Lessons, setup memory, exploration and every risk gate are
  unchanged.
- `graph`: `GRAPH_EXTRACT_MODE=heuristic` (new knob in `graph.extract`) and
  `GRAPH_BACKFILL_ENABLED=false` (backfill only re-ran old docs through the
  LLM). Publishing of `graph_signals` continues.
- Restore either with `MATRIX_LLM_BLEND_MODE=blend` / `GRAPH_EXTRACT_MODE=llm`
  in `.env` and `up -d --no-deps agent graph`.

### Contention before / after
Same 10-minute windows, 2026-10-09, crypto only, LLM backend healthy
(subscription, Haiku). Slots = `pg_locks` advisory locks in namespace
`0x4D415452`, sampled every 2 s (300 samples per window).

| | before (12:59:50–13:09:50) | after (13:10:40–13:20:40) |
|---|---|---|
| LLM calls, agent | 7 (p50 17.4 s) | **0** |
| LLM calls, graph | 40 (22 tool loops + 18 single-shot fallbacks) | **0** (heuristic extraction) |
| LLM calls, synthesis / director | 2 / 1 | 2 / 0 |
| imputed cost | $0.53 | $0.09 |
| global slots held: 0 / 1 / 2 / 3 / 4 | 123 / 149 / 22 / 6 / 0 samples | 236 / 64 / 0 / 0 / 0 |
| share of samples with ≥1 slot busy, holder | 59 %, graph in 69 % of them | 21 %, synthesis only |

Scaled to a weekday at full four-market load, the change removes ~1 400 agent
and ~5 900 graph calls (~$61 imputed). The agent's single-shot calls never
took a global slot, so "starving" was never slot-queueing: it was the shared
account's rate limit and the 18 s call latency inside the loop. Both are now
zero for the decision loop: a tick takes features + rule score, no network
wait.

## Re-entry criteria
- **LLM in the decision loop:** an A/B in which the LLM's side or its
  trade/hold choice beats the rule lean *in the same hours* at the promotion
  bar ([[methods]]), net of cost. The cheapest A/B is free: run the LLM on a
  shadow config only, or replay prompts offline on stored features.
- **LLM extraction:** a strategy whose replayed return depends on graph
  relations (contextual polarity, related companies) — measured with and
  without them — at the same bar.

## Caveats
- Every arm is net negative or indistinguishable from zero; this is a choice
  between ways of not having an edge. The money question is unchanged
  ([[strategy-scoreboard]]).
- The ε-exploration arm loses −11 net (t=−5.3, n=1 243) — it is the learning
  loop's sample budget, paper-only, and gated by slots and the EV floor; it is
  noted here, not changed.
- Conflicts (rule and LLM opposite) were holds and are not in `predictions`,
  so their counterfactual is unmeasured; rule non-exploration trades in
  LLM-up hours were 25 episodes in total, so the exposure is small.

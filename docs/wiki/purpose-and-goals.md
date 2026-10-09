---
title: Purpose and goals
updated: 2026-10-09
sources: [CLAUDE.md, docs/VISION.md, docs/AUTONOMY_PLAN.md]
status: current
---

Matrix is a self-updating, context-graph based, multi-market research and
**autonomous trading** engine. It has one primary goal: **make money**. Every
technical decision is filtered by "does this improve live PnL, directly or by
speeding the learning loop". The newsletter product is deferred.

## Claims
- **The only non-negotiable constraint** is the risk framework: risk gates and
  the paper-trade certificate ([[risk-gates]]). They protect the *durability*
  of profit, so they are part of the goal, not a tax on it.
- **The system is meant to run with no human in the loop.** The plan for this is
  `docs/AUTONOMY_PLAN.md`; as of 2026-09-19 all but one of its §9 items are
  done. Only three decisions remain human by design:
  `LIVE_EXECUTION_ENABLED`, `LIVE_CAPITAL_CAP_USD`, and mainnet API keys.
- **Success gates and where they stand (2026-09-19; G1/G3 re-checked 2026-10-09):**

| Gate | Definition | State |
|---|---|---|
| G1 continuity | 30 days of uninterrupted ticks | ❌ clock reset again on 2026-10-09. From 09-29 to 10-09 nothing traded: the VM lost egress for 6.3 days, then a flat battery cost 2.9 days. A host watchdog now alerts from outside the VM ([[incidents]]) |
| G2 closed learning loop | every applied mutation measured after 7 days, negatives auto-reverted | ✅ mechanism runs; too few samples to have judged much |
| G3 positive paper EV | ≥1 strategy, 60 days, n≥200, PnL>0 after costs, DD<15%, 95% CI lower bound >0 | ❌ nothing in the book has edge (2026-10-09). The one candidate is the shadow `neg_funding_carry`, with 0 closed episodes; see [[pnl-reality]] |
| G4 self-repair | dev_agent patch is tested and merged, effect measured | 🟡 chain works end to end; one real patch adopted after manual review |

- **Live capital is blocked until G3 passes.** That is a code-level gate, not a
  policy statement.

---
title: Matrix wiki index
updated: 2026-09-19
status: current
---

Compiled knowledge about this repository. Maintained by the LLM under the
`llm-wiki` skill: pages are updated in place, numbers carry dates, claims cite
their source. Machine- and operator-level knowledge lives in the global wiki
(`~/.claude/wiki/`).

Start with [purpose-and-goals](purpose-and-goals.md), then
[pnl-reality](pnl-reality.md) — the second is the honest answer to "is it
working".

## What it is
- [purpose-and-goals](purpose-and-goals.md) — the one goal, the success gates, where they stand
- [architecture](architecture.md) — shape of the system, data flow, two DB tiers
- [services](services.md) — every container, what it does, its cadence
- [data-model](data-model.md) — the tables that matter and which tier owns them

## What it does
- [strategies](strategies.md) — the signal catalogue and how a config becomes trades
- [paper-engine](paper-engine.md) — how a prediction becomes a position and an outcome
- [pnl-reality](pnl-reality.md) — measured economics: why it loses money
- [edge-study](edge-study.md) — controlled test of entry timing vs random entry
- [methods](methods.md) — quant methodology applied and queued, with why
- [learning-loop](learning-loop.md) — reflection, labs, lessons, memory, and their gates
- [risk-gates](risk-gates.md) — what stands between the code and real capital

## How it runs
- [operations](operations.md) — commands, environment knobs, daily checks
- [llm-stack](llm-stack.md) — model access, cost accounting, rate budget
- [development](development.md) — tests, hot reload, staging rules in a shared tree
- [incidents](incidents.md) — what has gone wrong, root causes, fixes
- [open-questions](open-questions.md) — what is unknown and what would settle it

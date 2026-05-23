# Node-Based Work Distribution

> Matrix runs on **N nodes** (1, 2, 3, ... as you add machines). There are no fixed "Machine A" / "Machine B" roles. Instead, each node declares which **roles** it can fulfill, and a coordinator (initially: simple Postgres-backed claim table) hands out work.

## The principle

Old model: "Machine A does Python, Machine B does Next.js" — too rigid; adding a 3rd PC required redesigning.

New model: **Every node is a worker. Roles are tags. Work items are claimed from a queue.**

## Roles a node can declare

A node's `.matrix-node.json` declares one or more roles:

```json
{
  "node_id": "muhsin-mbp-2024",
  "roles": ["ingestion", "graph", "strategy", "backtest", "web", "dev"],
  "capacity": {
    "max_concurrent_jobs": 4,
    "gpu": false
  }
}
```

**Standard roles** (extend as needed):

| Role | What it does |
|---|---|
| `ingestion` | Runs ingestion connectors (long-running daemons) |
| `graph` | Entity extraction + graph upsert workers |
| `strategy` | Generates predictions from current context |
| `backtest` | Heavy historical replay + paper-trade engine |
| `reflection` | Daily/weekly self-improvement agents |
| `execution` | Sends actual orders (testnet or live) — only nodes with this role can submit orders |
| `web` | Runs the Next.js dashboard / bulletin site |
| `dev` | Available for active coding work via Claude Code (every node) |

A node can have many roles. A laptop may have all roles. A small home server may run just `ingestion` + `graph`.

## Claim mechanism (initial — simple)

A Postgres table `jobs(id, role, payload, claimed_by, claimed_at, completed_at, ...)`. Nodes poll for `WHERE role IN (my_roles) AND claimed_by IS NULL` with row-level locking (`FOR UPDATE SKIP LOCKED`). Standard pattern, works at small scale, no extra infra.

Upgrade path when this is the bottleneck: NATS / Redis Streams / Vercel Queues. Not before.

## Dev work (the Claude Code dimension)

Every node also has the `dev` role — meaning Claude Code on that node can pick up coding tasks. Coordination:

- `docs/CHANGES.md` is the append-only log of cross-cutting changes (still authoritative)
- TODO tasks tagged in `docs/TODO.md` can be claimed by any dev node — assign your `node_id` next to the task
- For independent feature work, just `git pull --rebase` frequently and push small commits

## File ownership (best practice, not enforced)

Path | Anyone can edit?
---|---
`apps/web/` | Yes (web role usually does it, but any dev node can contribute)
`services/<x>/` | Yes (the node running that service has implicit veto on breaking changes)
`packages/` | Yes
`infra/db/` | Migrations should be authored by one node at a time — coordinate via `docs/CHANGES.md`
`docs/`, `CLAUDE.md` | Anyone, additive merges preferred

## Conflict avoidance

1. **Pull before starting work** — `git pull --rebase`. Always.
2. **Push small + often** — every meaningful unit, ideally hourly.
3. **Cross-cutting change?** One line in `docs/CHANGES.md` BEFORE coding.
4. **Conflict on docs:** keep both sides — additive is almost always correct.
5. **Conflict on code:** rebase, fix, push. If too structural, stop and ping the user.
6. **Schema migrations:** one author at a time. Add a note in `docs/CHANGES.md` before authoring.

## Onboarding a new node (any PC)

```bash
# 1. Clone the repo
git clone <local-or-remote-url> matrix
cd matrix

# 2. Declare this node
cp .matrix-node.example.json .matrix-node.json
# Edit node_id + roles

# 3. Boot local infra (only one node needs to run this if they share a network)
docker compose up -d

# 4. Install
pnpm install
# For each service this node will run:
cd services/<service> && uv sync

# 5. Start the daemon(s) for declared roles
# (process management script TBD — likely a Makefile target per role)
```

## Why this matters

You explicitly said: "öteki bilgisayar başka bir PC'de de çalıştırılabilir olmalı." This design honors that — adding a 3rd or 4th node is now a config change, not an architecture change. The system scales horizontally as you add hardware.

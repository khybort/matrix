# Two-Machine Work Split

## The principle

Split by **domain/language**, not by feature. Each machine has a self-contained scope where it can iterate without coordination overhead. The language boundary (Python ↔ TypeScript) provides natural conflict isolation.

## Machine A — "Cortex"

**Identity file**: `echo cortex > .matrix-machine`

**Owns**:
- `services/` (all Python services: ingestion, graph, strategy, backtest)
- `packages/python-shared/`
- `infra/db/` (schema + migrations — TS side reads schema but doesn't author it)
- Python dependencies (`pyproject.toml`, `uv.lock` in each service)

**Long-running processes on this machine**:
- Ingestion daemons (always-on)
- Reflection agents (cron-scheduled)
- Paper-trade loop (continuous)
- Graph upsert workers (event-driven)

**Typical workday for the Claude Code agent on Machine A**:
- Implement a new connector
- Tune entity extraction prompts
- Add a strategy module
- Debug a graph upsert race condition
- Improve backtest fidelity

---

## Machine B — "Output"

**Identity file**: `echo output > .matrix-machine`

**Owns**:
- `apps/web/` (Next.js dashboard + bulletin site)
- `packages/ts-shared/`
- Vercel project (`vercel.ts`, deployment config)
- Content/marketing (Substack drafts, X posts)
- TS dependencies (`package.json`, `pnpm-lock.yaml`)

**Long-running processes on this machine**:
- `pnpm dev` for live editing
- Content drafting/iteration
- Deployment monitoring

**Typical workday for the Claude Code agent on Machine B**:
- Build dashboard pages
- Improve bulletin layouts
- Wire up auth/billing
- Set up Stripe webhooks
- Write or improve content templates
- Optimize Core Web Vitals

---

## Shared territory (touch with care)

| Path | Rule |
|---|---|
| `docs/` | Both machines can edit. Merge conflicts here are usually additive — keep both sides. |
| `CLAUDE.md` | Both can edit. Flag in `docs/CHANGES.md`. |
| `docs/CHANGES.md` | Append-only changelog of cross-cutting changes. |
| Root configs (`package.json`, `pnpm-workspace.yaml`, `.gitignore`) | Either can edit; commit promptly. |
| Env templates (`.env.example`) | Either can edit when adding new env vars. |

## Conflict avoidance protocol

1. **Always pull --rebase before starting work** — non-negotiable.
2. **Push frequently** — every meaningful unit of work, ideally hourly.
3. **Cross-cutting change?** Write one line in `docs/CHANGES.md` BEFORE coding it, so the other machine sees it in the next pull.
4. **Merge conflict?** If on docs: keep both. If on code: rebase, fix, push. If structural: stop, ping user.
5. **Schema change?** Owner: Machine A. Machine B consumes via generated types. Never edit migrations from Machine B.

## When in doubt

If you're on Machine B and need to call a service that doesn't exist yet, **don't** scaffold Python yourself. Add a `TODO: needs <service-name>.<endpoint>` in `docs/CHANGES.md` and ping the user. Machine A will pick it up.

Same in reverse: Machine A doesn't write Next.js pages. Note required UI in `docs/CHANGES.md`.

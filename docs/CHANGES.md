# Cross-cutting Changes Log

> Append-only. Each entry: date + machine + one-line description of a cross-cutting change.
> Read this on every pull. If you're about to make a change that affects both Python and TS sides, add an entry here BEFORE coding.

---

## 2026-05-23 — Bootstrap (initial author: assistant, on behalf of user)

- Foundation docs created: `VISION.md`, `ARCHITECTURE.md`, `ROADMAP.md`, `WORK_SPLIT.md`
- `CLAUDE.md` written with two-machine instructions
- Monorepo skeleton scaffolded (pnpm workspace)
- Tech stack defaults locked in `docs/ARCHITECTURE.md` (Next.js 16 + Python 3.13 + Neon + pgvector + AGE)
- Next action: Phase 0 — git remote setup, Neon project, initial schema

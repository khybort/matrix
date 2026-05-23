# Roadmap

> Time-boxed milestones with explicit validation gates. Slipping a gate triggers a reassessment, not just "push the date right."

## Phase 0 — Foundation (Week 0, current)

- [x] Vision + architecture + work-split docs
- [x] Monorepo skeleton
- [x] CLAUDE.md for both machines
- [ ] Git remote (decision pending: GitHub private?)
- [ ] Neon project + initial schema (`infra/db/`)
- [ ] `.env.example` + secrets management plan

**Gate:** Both machines pull the repo, run `pnpm install` and `uv sync` cleanly.

---

## Phase 1 — Single market, single strategy, single-shot (Weeks 1-2)

Goal: prove the **end-to-end loop** on the smallest possible scope before scaling out.

**Choice for first market:** US equities — earnings calendar + 10-Q/10-K filings (free via SEC EDGAR). Reasons: free data, global English-speaking audience for eventual bulletin, deep LLM-augmentable content.

- [ ] Ingestion connector: SEC EDGAR (filings) + a free news source (e.g. Finnhub free tier or aggregated RSS)
- [ ] Entity extraction → minimal graph (Company, Document, Event nodes; MENTIONS, IMPACTS edges)
- [ ] One strategy module: "earnings surprise reaction" — predict 5-day post-earnings drift based on call sentiment + estimate beat/miss
- [ ] Storage: predictions table + outcomes table, scored after 5 trading days
- [ ] Daily cron: run loop on yesterday's earnings; produce JSON report

**Gate:** End-to-end loop runs daily for 2 weeks, with zero manual intervention, producing scored predictions.

---

## Phase 2 — Reflection + multi-strategy (Weeks 3-6)

- [ ] Reflection agent: weekly run that scores strategies + mutates underperformers
- [ ] Add 2 more strategy modules (e.g. "guidance change reaction", "insider buying clusters")
- [ ] Graph queries: enable cross-entity reasoning ("show all events involving X cluster")
- [ ] Begin paper-trade ledger — track hypothetical PnL with realistic execution assumptions
- [ ] Internal dashboard MVP (apps/web): list predictions, view graph snippet, see scores

**Gate:** 4 weeks of paper-trade track record across 3+ strategies, with at least one showing positive expected value.

---

## Phase 3 — Public-facing surface (Weeks 7-10)

- [ ] Public bulletin site (apps/web): blog-style daily summary
- [ ] First connector for a second market — likely crypto (rich free data, 24/7)
- [ ] Substack/X content pipeline: auto-publish a sanitized daily brief
- [ ] Auth + waitlist for paid tier (no charging yet)

**Gate:** 50+ subscribers on free tier OR 500+ X/Substack followers.

---

## Phase 4 — Monetize (Weeks 11-16)

- [ ] Stripe + Clerk billing wired
- [ ] Paid tier launched: $49/mo intro pricing
- [ ] Premium daily report (deeper, ungated for paid)
- [ ] Add BIST connector (Turkish market — local edge, niche audience expansion)

**Gate:** First paying subscriber within 4 weeks of launch.

---

## Phase 5 — Scale loop, grow MRR (Weeks 17-26)

- [ ] FX/macro connector
- [ ] Cross-market signal correlations exposed in product
- [ ] Track record page (public proof, builds trust)
- [ ] Referral program
- [ ] Power-user features: alert subscriptions, custom watchlists

**Gate:** $1k MRR by week 26.

---

## Beyond 6 months (sketch only)

- API access tier
- Multi-tenant team accounts
- Custom research-on-demand (productized service)
- Partnerships with smaller funds for licensed feed
- Asset preparation: clean books, audit logs, exportable graph, valuation prep

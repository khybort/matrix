# Architecture

> Living document. Changes here should be flagged in `docs/CHANGES.md` so the other machine catches them.

## System diagram (logical)

```
                ┌─────────────────────────────────────────────────────────────┐
                │                      EXTERNAL SOURCES                       │
                │  Market APIs · News feeds · Filings · On-chain · User input │
                └────────────────────────────┬────────────────────────────────┘
                                             │
                                             ▼
                              ┌──────────────────────────────┐
                              │   services/ingestion         │  Machine A
                              │   (connectors per source)    │
                              └──────────────┬───────────────┘
                                             │ raw_documents
                                             ▼
                              ┌──────────────────────────────┐
                              │   services/graph             │  Machine A
                              │   - entity extraction (LLM)  │
                              │   - relation extraction      │
                              │   - dedup & merge            │
                              │   - graph upsert             │
                              └──────────────┬───────────────┘
                                             │
                                             ▼
   ┌─────────────────────────────────────────────────────────────────────────┐
   │                            NEON POSTGRES                                │
   │                                                                         │
   │   relational (events, documents, predictions, outcomes, subs)           │
   │   + pgvector (embeddings, semantic search)                              │
   │   + Apache AGE (graph: nodes, edges, traversal)                         │
   │   + tsvector (full-text)                                                │
   └───────────────────────┬─────────────────────────┬───────────────────────┘
                           │                         │
                           ▼                         ▼
       ┌──────────────────────────────┐   ┌──────────────────────────────┐
       │   services/strategy          │   │   apps/web                   │
       │   - signal generation        │   │   - dashboard (internal)     │
       │   - prediction tracking      │   │   - bulletin site (public)   │
       │   - LangGraph agents         │   │   - subscription/billing     │
       │   Machine A                  │   │   Machine B (Vercel)         │
       └──────────────┬───────────────┘   └──────────────────────────────┘
                      │
                      ▼
       ┌──────────────────────────────┐
       │   services/backtest          │  Machine A
       │   - replay historical        │
       │   - score predictions        │
       │   - feed learning loop       │
       └──────────────────────────────┘
                      │
                      ▼
       ┌──────────────────────────────┐
       │   reflection agents          │  Machine A — runs on cron
       │   - synthesize patterns      │
       │   - mutate prompts/strategies│
       │   - retire underperformers   │
       └──────────────────────────────┘
```

## Context graph design

**Node types** (initial — extend as needed):
- `Company` (ticker, name, exchange, sector)
- `Person` (executive, analyst, fund manager)
- `Event` (earnings, M&A, regulatory, macro)
- `Document` (filing, news article, transcript)
- `Asset` (equity, option contract, crypto token, FX pair)
- `Concept` (theme, sector trend, macro factor)
- `Prediction` (signal we generated, with timestamp + thesis)
- `Outcome` (later-observed market reaction; binds to prediction)

**Edge types**:
- `MENTIONS` (Document → Company/Person/Asset)
- `EMPLOYS` (Company → Person)
- `IMPACTS` (Event → Company/Asset)
- `CORRELATED_WITH` (Asset ↔ Asset, with computed coefficient)
- `PREDICTS` (Prediction → Asset, with confidence + horizon)
- `RESULTED_IN` (Prediction → Outcome)
- `EVOLVED_FROM` (Concept → Concept; tracks taxonomy drift over time)

**Why graph + vector + relational together:**
- Graph: "what events involved both Company X and Person Y?"
- Vector: "find documents semantically similar to this earnings call passage"
- Relational: structured facts (price series, fundamentals)
- Hybrid retrieval = best-of-all-worlds context for the LLM

## Self-learning loop

1. **Prediction emitted** → stored with full thesis + retrieved context + timestamp
2. **Outcome window passes** (configurable per strategy: hours/days/weeks)
3. **Outcome scored** by market reality (price move, event resolution, etc.)
4. **Score back-propagated** to: the strategy module, the specific prompt template, the retrieval method used
5. **Periodic reflection agent** (daily) examines aggregate scores → proposes prompt mutations, retires underperformers, promotes new strategies to "live" tier

This loop is what makes the system *appreciate* in value rather than stay static.

## Tech stack (defaults — change deliberately)

| Layer | Choice | Rationale |
|---|---|---|
| Web framework | Next.js 16 App Router | Vercel-native, App Router maturity |
| UI | Tailwind + shadcn/ui | Fast, owned components |
| AI orchestration (web) | Vercel AI SDK v6 via AI Gateway | Provider-agnostic, observability |
| Engine language | Python 3.13 | ML/data ecosystem dominance |
| Engine API | FastAPI | Async, Pydantic-native, typed |
| Agent framework | LangGraph | Graph-based agent state machines fit our use case |
| Data manipulation | Polars | Fast, lazy eval, modern API |
| DB | Neon Postgres | Vercel marketplace native; branches for safe migration |
| Graph extension | Apache AGE | Cypher in Postgres — no extra ops |
| Vector | pgvector | Same DB; HNSW indices |
| Auth | Clerk | Vercel marketplace, fastest path |
| Payments | Stripe (or via Clerk billing) | Standard |
| Email/bulletin | Resend + Buttondown/Substack | Initial: Substack for audience; eventually self-host |
| Hosting (web) | Vercel | Default |
| Hosting (engine) | Local machines (initial), Fly.io/Hetzner (scale) | Cost-effective for long-running |

## Cost discipline

- Embeddings: use cheap models (text-embedding-3-small or open-source) for most content; reserve expensive only for high-value synthesis
- LLM calls: Haiku 4.5 for filtering/extraction, Opus 4.7 only for final synthesis
- Cache aggressively via Vercel Runtime Cache (web) and Postgres-backed cache (engine)
- Anthropic prompt caching (5min TTL) — structure all multi-call workflows to reuse cached prefixes

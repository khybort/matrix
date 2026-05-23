# Vision

## The thesis

Three trends collide in 2026:

1. **LLMs can read** — earnings calls, filings, 8-Ks, on-chain transactions, breaking news. Domain analysts are still bottlenecked by hours-per-document.
2. **Graph + vector hybrid retrieval works** — Graph RAG showed that entity-relationship graphs dramatically outperform pure vector search for connected reasoning.
3. **Self-improvement loops are tractable** — every prediction can be timestamped, then graded against later market reality. Automated reinforcement is achievable without RLHF infrastructure.

Matrix combines all three into a single, continuously-learning research engine **that trades the markets itself**.

## Primary product: autonomous, self-improving trading system

The system:
1. Ingests data continuously (prices, order books, news, on-chain) into a persistent context graph
2. Generates trading signals via versioned strategy modules
3. Executes trades (paper first, then small live capital) through broker APIs
4. **Scores every trade against outcome** — feeds the score back to the strategy/prompt that generated it
5. **Mutates underperforming strategies/prompts automatically**, retires losers, promotes winners
6. Repeats. Forever. Across multiple nodes.

The "compound interest" is in the **graph + strategy library** that improves with every cycle.

## Secondary product (deferred): research bulletin

Once the autonomous trading system has a meaningful track record (post-paper-trade validation, post-first-live-PnL milestone), the same engine produces sanitized research bulletins for paid subscribers. This is **not the primary thesis** — it's a downstream monetization layer that appears only after the engine has proven itself.

## Income roadmap (revised)

**Months 1-4 — Build & validate (no income expected)**
- Local infra up, ingestion + graph + first strategy live
- Paper-trade engine running 7/24 across multiple strategies
- Self-improvement loop producing measurable strategy-quality drift
- Risk management layer in place

**Months 4-6 — Live capital exploration**
- Best paper-trade strategies promoted to **small live capital** (target: $500-2000 risk capital, NEVER more than you can lose entirely)
- Goal here is not "make money" — it's to discover the paper-vs-live performance gap and adapt
- Reflection agents now tune for live-market reality, not historical replay

**Months 6-12 — Scale what works**
- If a strategy survives 90+ days live and is risk-adjusted positive: scale capital cautiously (Kelly fraction sizing, never full Kelly)
- Add more markets via connectors as bandwidth allows
- Begin spinning out the bulletin product using the validated track record

**Years 2-3 — Asset state**
- Combined PnL track record + the graph itself + auto-tuned strategy library + (now-running) bulletin subscriber base = sellable IP
- Target: $20k+ / month combined PnL + bulletin MRR, or one-time acquisition exit

## The brutal honesty section

**Most retail autonomous trading systems lose money.** This is statistically uncontroversial. Why Matrix has a shot:

- **No human decision-making bottleneck** — sentiment changes from news → graph → strategy → order in seconds, not hours
- **Auto-tuning rather than static strategies** — the strategy library is allowed to evolve; bad ones die
- **Graph context** — most retail bots react to single-data-point signals; Matrix reasons over connected events
- **Cost discipline** — Haiku 4.5 for high-volume filtering, Opus only for high-stakes synthesis; embedding caches; aggressive deduplication

**Why it still might fail:**
- Market structure changes faster than reflection agents adapt
- LLM costs eat margins if not relentlessly optimized
- Broker counterparty risk (especially in crypto)
- Bugs that drain capital before circuit breakers trip — **this is the catastrophic risk; risk-management discipline is the only defense**

See `docs/TRADING.md` for the risk framework that protects against the catastrophic case.

## Why this could be a large asset in 2-3 years

If autonomous trading + auto-tuning + graph context works at any scale, the **engine itself + its track record + its accumulated graph** are uniquely valuable. Either:
- Run it as a personal capital allocator (compound your own money)
- License the engine + strategy library to a small fund
- Sell the bulletin business once it has subscribers
- Full acquisition by a research firm or fund interested in the tech

None of these require building a SaaS sales motion. The asset speaks for itself.

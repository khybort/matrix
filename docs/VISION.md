# Vision

## The thesis

Three trends collide in 2026:

1. **LLMs can read** — earnings calls, filings, 8-Ks, on-chain transactions, breaking news. Domain analysts are still bottlenecked by hours-per-document.
2. **Graph + vector hybrid retrieval works** — Graph RAG paper (Microsoft) showed that entity-relationship graphs dramatically outperform pure vector search for connected reasoning.
3. **Self-improvement loops are tractable** — every prediction can be timestamped, then graded against later market reality. Automated reinforcement is achievable without RLHF infrastructure.

Matrix combines all three into a single, continuously-learning research engine.

## The two products

### Product 1: Internal — "Second Brain"

A query interface where Muhsin can ask anything across the entire ingested context, with graph-augmented answers in seconds. This is not the revenue product, but it's the **founder's edge**: a personal cognitive prosthesis that compounds in value.

### Product 2: External — Research Bulletin

Daily/weekly machine-generated research, distributed via:

- Free tier (Substack/X/email): summary briefings, audience-building
- Paid tier ($49-149/mo): full signals, position guidance, deeper analysis, alerts
- Eventually: API access for power users / small funds

Revenue target ramp:
- Month 4: First paid sub
- Month 6: $1k MRR
- Month 12: $5k MRR
- Year 2-3: Acquirable asset territory

## Why this could fail (honestly)

- **Market signals are noisy.** Most quant strategies fail. Mitigation: revenue isn't dependent on PnL — the bulletin sells *quality of analysis*, not *PnL claims*.
- **Audience-building is slow.** 6 months to $1k MRR requires consistent content. Mitigation: machine-generated daily output is the differentiator; no human writer's block.
- **LLM cost.** Continuous ingestion + reflection can burn tokens. Mitigation: Vercel AI Gateway + Haiku for filtering, Opus only for synthesis; aggressive caching; local embeddings.
- **Regulatory.** Investment advice rules vary by jurisdiction. Mitigation: positioned as "research and analysis", not "personalized advice"; no order execution.

## The 10-year bet

If this works at any scale, the **context graph itself is the asset**. A 2-year-old self-curated graph of financial entities, events, relationships, and graded predictions is uniquely valuable. Either acquired by a research firm/data vendor, or kept as a personal alpha generator.

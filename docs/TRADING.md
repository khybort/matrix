# Trading — Risk Framework, Broker Strategy, Regulatory Notes

> This is the most important document in the project. **Every live-capital decision is governed by what is written here.** Read it before touching any code in `services/strategy`, `services/backtest`, or anything that talks to an exchange API.

## The honest baseline

- **Most retail autonomous trading systems lose money.**
- Backtest performance is **almost always optimistic** vs. live performance.
- Software bugs can drain capital in seconds — humans react in minutes.
- LLM costs can quietly destroy margins if not monitored.

The system below is designed to survive these realities, not pretend they don't exist.

---

## Phased capital exposure

| Phase | What's risked | Allowed broker mode | Sign-off |
|---|---|---|---|
| 0-4 | $0 (paper-trade only) | Testnet (Bybit/Binance) | None |
| 5 | $500-2000 max, "lose-it-all-OK" | Mainnet, small live keys | User confirmation + 60d paper history + positive risk-adjusted return |
| 6+ | Scale on validated strategies | Mainnet | Per-scale-step user confirmation, never automatic |

The user defines "lose-it-all-OK" before any mainnet key enters the system. That number is the ceiling, not a target.

---

## Hard limits enforced in code (not config best-practices, code)

These live in `services/execution` (or wherever order submission happens) and **cannot be overridden by a strategy module**:

1. **`MAX_POSITION_PCT`** — single trade can never exceed N% of current account equity (default 2%)
2. **`DAILY_LOSS_CIRCUIT_PCT`** — if today's PnL < -N% of starting-of-day equity, all positions close + new orders blocked until manual reset (default 5%)
3. **`LIVE_CAPITAL_CAP_USD`** — total capital deployed across the system cannot exceed this; new orders that would breach it are rejected
4. **`LIVE_EXECUTION_ENABLED`** — global kill flag. Default `false`. Setting `true` requires manual edit of `.env.local` on the execution node.
5. **Per-strategy lifecycle gate** — a strategy cannot graduate to live execution without `paper_trade_certificate` row in DB showing 60+ days of monitored runs.

Tests in `services/execution` MUST cover:
- Order rejection when limits breached
- Circuit breaker triggering and reset
- Strategy promotion blocked without certificate

---

## Broker selection (first market = crypto)

**Default: Bybit** (testnet for Phase 1-4, mainnet for Phase 5+).

Reasons over Binance:
- Cleaner API for derivatives (less rate-limit babysitting)
- Testnet is feature-complete (Binance testnet has gaps)
- Lower fees on small accounts

Reasons against (acknowledge):
- Counterparty risk — exchange could freeze withdrawals, get hacked, regulator action
- Mitigation: **never keep more than `LIVE_CAPITAL_CAP_USD` on the exchange**, sweep PnL above buffer to cold storage weekly

**Alternative if Bybit declines our region:** Binance, OKX, Bitget. Pick one, commit, don't multi-exchange until validated single-exchange flow.

## Why crypto first (and not equities)

- 24/7 markets → both nodes have work to do continuously
- Testnet is fully functional (US equity paper trading via brokerages exists but is friction-heavy)
- Smaller minimum capital
- Fewer regulatory traps for a Turkish-resident developer running an unincorporated system
- Sub-second execution rounds give the self-improvement loop more iterations per week

**Why we'll still add equities later (Phase 6+):**
- Bulletin product audience is bigger in equities
- Equities have richer "fundamental" data (filings, earnings calls) that play to the LLM/graph strengths
- Crypto is a good proving ground; equities are a bigger market

---

## Regulatory positioning

User is in Turkey. The system, as designed:

1. **Trades the user's own capital** — no client funds, no investment advice to third parties → outside investment-advisor regulatory perimeter in most jurisdictions
2. **No personalized recommendations to others** during Phases 0-5
3. **Bulletin product** (Phase 6+) positioned as **research and analysis** — generic, not personalized — to stay outside advisor regulation. Need legal sanity check before paid tier launches.

This is not legal advice. Before going paid-bulletin or accepting any external capital, consult a Turkish lawyer familiar with SPK regulations on financial content.

---

## Tax bookkeeping

Every trade emits an audit row: `timestamp, exchange, symbol, side, qty, price, fee, strategy_id, prediction_id`. This is non-optional. Future-you (and your accountant) need it. Build the audit log in Phase 3, not retrofit it later.

---

## What "self-improvement" can and cannot tune

**Allowed to mutate automatically:**
- Prompts inside strategy modules
- Retrieval recipes (graph traversal depth, vector top-k)
- Strategy parameters within configured bounds (e.g. confidence threshold)
- Promotion/demotion of strategies based on outcome scores

**Forbidden to mutate automatically:**
- Risk limits (`MAX_POSITION_PCT`, `DAILY_LOSS_CIRCUIT_PCT`, `LIVE_CAPITAL_CAP_USD`)
- `LIVE_EXECUTION_ENABLED` flag
- Broker API endpoints / keys
- Database migrations
- Anything that increases capital exposure beyond user-set caps

This separation is what keeps the system from optimizing itself into ruin.

---

## Anti-patterns to refuse

If a future contributor (human or AI) proposes any of these, **say no**:

- "Let's let the reflection agent adjust position size based on win rate" — no. Risk caps are static.
- "Increase the daily loss circuit to give strategies more room" — no, unless explicit user decision.
- "Run live and testnet in parallel from the same orderbook stream" — no. Strict isolation.
- "Skip the paper-trade certificate for this one strategy, it looks really good" — no.
- "Move the kill switch to a config file the reflection agent can edit" — absolutely no.

---

## The one-line rule

**If something can drain capital, it cannot be automated without a human-set, code-enforced ceiling.**

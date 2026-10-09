---
title: Carry execution (two-leg path)
updated: 2026-10-09
sources: [packages/python-shared/src/matrix_shared/{carry_executor,carry_venues,live_gate}.py, services/execution/tests/{test_carry_executor,test_carry_precheck_parity}.py, services/backtest/src/backtest/{paper_trade,carry_precheck_replay}.py]
status: current
---

How a spot-hedged carry (`neg_funding_carry`: long the Bybit perp, short the
coin borrowed on Bybit spot margin or Binance cross margin, 48 h) would reach
an exchange. Built 2026-10-09 so the path exists before the evidence. It is
**not enabled**: dry-run is the default, the paper-engine mirror is always
dry-run, and there is no mainnet mode. See [[risk-gates]] and
[[signal-research-2026-10]].

## Claims
- **One state machine, two venue layers.** `CarryExecutor`
  (`matrix_shared.carry_executor`, re-exported by `execution.carry`) runs the
  same steps in every mode. `_SimOps` (dry-run) and `_LiveOps` (testnet) share
  every request builder (`matrix_shared.carry_venues`), so dry-run builds,
  signs and logs the requests a live run would send.
- **Leg order.** Open: borrow, sell spot (IOC), buy perp (IOC), then repay
  whatever borrow the hedge does not use. Close: buy spot, sell perp
  (reduce-only), repay. Both directions run the thin, uncertain spot leg
  first. A failed open spot leg exposes nothing, and a failed close buyback
  leaves the hedge on. This deliberately differs from a plain reverse.
- **Never a naked leg.** If the open's perp leg fails, the spot is bought back
  at once with a bounded-slippage IOC (100 bps, then 200 bps) and the borrow
  is repaid: status `unwound`, plus a warning alert. If a close sells the
  perp short of the coin bought back, the perp sell is retried at 30, 100 and
  200 bps. If an unwind or close retry still fails, the status is `naked`,
  a critical alert fires (log + Telegram), and the kill switch trips: a
  process latch refuses every later open, and the wallet's
  `circuit_tripped_at` is set, which the gate honours. Dry-run only records
  `would_kill`.
- **Bounded slippage everywhere.** Every order is Limit IOC at mid ± a band
  (`MATRIX_CARRY_EXEC_BAND_BPS` 30, `..._UNWIND_BAND_BPS` 100). There are no
  unbounded market orders.
- **Partial fills.** A partial spot sell is hedged at the filled qty and the
  excess borrow repaid. A partial perp buy is retried once. Any remaining
  excess spot is bought back. The status is then `partial` with the hedged
  qty. Spot buys are grossed up for the fee, which Bybit takes in the coin.
- **Pre-trade checks, all before any order:** live gate; the borrow quote now
  must be ≤ 1.5 × the quote the signal priced (`margin_borrow_rates`); the
  borrow quota (Bybit `/v5/order/spot-borrow-check` `maxTradeQty`, Binance
  `/sapi/v1/margin/maxBorrowable`); available margin ≥ combined notional ×
  `MATRIX_CARRY_EXEC_MARGIN_BUFFER` (1.0, unlevered); then the borrow itself.
  If the borrow is rejected, the carry aborts before any trade. The four
  checks (books, borrow drift, quota, margin) are **one function,
  `precheck_open`**, which paper calls too (next section). Each check is
  `pass | fail | unknown | skipped`; `unknown` (no recorded quote, private
  data) never blocks. The result is recorded on every open
  (`carry_exec_<mode>.open.precheck`).
- **Caps on a two-leg position.** The gate is called with the **combined
  notional of both legs**. The per-leg size is the minimum of: the paper size,
  $500 until `confirmed`, and `max_position_pct × equity / 2`. Since
  2026-10-09 the paper engine sizes carries by the same rule
  (`paper_trade.carry_leg_cap`, every carry side, every sizing branch incl.
  Kelly), so the paper leg **equals** the executor's leg: at equity $9 841 and
  the 2 % cap, $98.41 a leg on both sides. Before, paper booked $196.82 a leg
  (KAIA, SKL) against the full cap, i.e. 2× live dollars; bps were never
  affected. The dry-run mirror's remaining size gap is the qty-step rounding
  (re-run on the three open neg_funding_carry positions: paper $98.40, executor
  $98.35/$98.37/$98.40). `LIVE_CAPITAL_CAP_USD`, the certificate, posture,
  circuit and slot caps apply unchanged through `should_submit_live`. Closes
  call it with `closing=True`.
- **Idempotency.** Client order ids are deterministic per (prediction,
  action, leg, attempt), 24 chars: `orderLinkId` on Bybit, `newClientOrderId`
  on Binance. A duplicate-id answer is resolved by querying that id. Each
  result is written to `predictions.context.carry_exec_<mode>.<open|close>`
  in a single-statement jsonb merge. A second open of the same prediction is
  a no-op. Testnet writes an `in_flight` marker before borrowing: a rerun
  after a crash refuses and alerts instead of borrowing again.
- **Rate limits.** Each venue has a request token bucket (5/s). Opens have a
  bucket of 6 per hour (`MATRIX_CARRY_EXEC_MAX_OPENS_PER_HOUR`), which
  refuses rather than waits, so bug-driven bursts are cut off. The expected
  cadence is about 50 a week.
- **Reconciliation.** In testnet it reads the perp position and the coin's
  liability back from the venue and checks them against the intended hedge
  (`reconcile.matches`). Every result records each leg's fill, its slippage
  vs mid, and `gap_bps` = executable − paper walk per leg (`book_open` at
  open, `book_close` at close).

## Venue status (checked 2026-10-09)
| venue | role | testnet margin | mode available |
|---|---|---|---|
| Bybit (UTA) | perp leg; spot leg when it is the cheaper borrow | yes: `/v5/spot-margin-trade/data` lists 45 borrowable coins, 38 spot pairs `marginTrading=utaOnly`, `/v5/account/borrow` and `/repay` answer | dry-run, testnet |
| Binance cross margin | spot leg when cheaper (all 3 open NFC carries on 10-09) | **no**: `testnet.binance.vision/sapi/v1/margin/*` is 404 | dry-run only |

The testnet mode was not exercised against the venue. The env's Bybit key is
expired, and the testnet's margin coins (majors plus a few, e.g. MINA, LUNC)
rarely overlap the illiquid coins the strategy trades. The mocked-venue tests
cover the logic. Before the first testnet run, verify the
`/v5/account/borrow` and `/v5/account/repay` body fields
(`coin`, `amount`) against the current docs.

## Paper applies the executor's pre-trade checks (2026-10-09)
A dry-run on the open KAIA carry aborted (borrow quote ×1.96 since the
signal) while paper had opened it, so the shadow book could count episodes
live could never enter. Now `paper_trade` calls
`carry_executor.paper_open_precheck` before every book-priced carry open: the
same `precheck_open`, in the same order, with the same abort reasons, on what
live would see from public data — the executor's own DB books (≤ 60 s; paper's
REST fallback does not count), the recorded quote in `margin_borrow_rates`,
quota from its recorded `max_borrow` / `borrowable`, margin `unknown`
(account data is private; the dry-run executor says the same, so the two
agree). The parity test (`services/execution/tests/test_carry_precheck_parity.py`,
13 cases) feeds both the same inputs and requires the same decision, reason
and per-check statuses.

- **Fail** → no position; `predictions.context.exec_precheck =
  {status: skipped, failed, reason, checks, detail, ts}`, written when the
  decision changes. The signal is re-checked every tick until it opens or
  expires (live would retry the same way).
- **Pass** → the position's context carries `exec_precheck.status = pass`.
- **Mirror aborts seconds later** → the position stays; `exec_precheck.status
  = would_abort`, `source: mirror`.
- The shadow tracker and the carry evidence (`edge_study.carry_edge_rows`)
  drop `would_abort` positions ([[operations]] "Shadow tracker").
- Not covered: the venue's lot/notional minimums (REST specs; paper legs are
  ≈ $98, far above them) and the live gate itself (the certificate is the
  point of the shadow).

**Replay at entry vs now (2026-10-09 18:15 UTC,
`python -m backtest.carry_precheck_replay --since 2026-10-09 --now`):** all
3 NFC entries today pass **at their open time**; 0 refused, 0 tagged. KAIA
opened 14:04 UTC, before the borrow recorder's first row (16:16), so drift and
quota were `unknown` — but the signal priced the venue's public table 4 s
before the open, so live would have seen the same quote (×1.00). Its quote
then rose to ×1.50 at 17:02 and ×1.96 at 18:07: checked **now**, KAIA aborts
(`borrow_drift`), SKL and API3 pass. The finding's abort was a re-run hours
after entry. A quote that moves after entry is a hold cost (charged at close
from the series), not an entry the executor would have refused; tagging KAIA
on it would drop an episode on post-entry information and bias the evidence
up.

## Hard limits in code
- `HttpTransport` sends only to hosts in `carry_venues.SENDABLE_HOSTS` =
  `{api-testnet.bybit.com}`. Dry-run uses `NeverSend`, whatever transport the
  caller passes. Mainnet requires a human commit adding its host and a mode.
- Testnet mode refuses when `BYBIT_TESTNET=false`, when credentials are
  missing, or when the borrow venue has no testnet.
- dev_agent `FORBIDDEN_PATHS` covers `carry_executor.py`, `carry_venues.py`
  and `live_gate.py` (2026-10-09).

## Shadow mirror
`paper_trade` calls `mirror_paper_open` after every book-priced carry open,
and `mirror_paper_close` after its close. Both run the executor in dry-run
with a 5 s timeout, never raise, and read books from the DB only (≤ 60 s old,
no REST on the paper path). Instrument filters come from public REST, cached
for 6 h. A close without a dry-run open record (a carry opened before the
mirror) is skipped. The gate is evaluated and recorded but does not stop the
simulation. Off switch: `MATRIX_CARRY_MIRROR=false`. Log line (a live
dry-run on the open SKL carry, 2026-10-09 17:56 UTC):

```
carry_exec[dry_run] open SKLUSDT/binance pred=f0a4815f-… status=done gate=refused(1) qty=18943
leg=$98.41 combined=$196.82 spot_sell[binance] 18943/18943@0.0052 slip=+9.6bps paper=+9.6
perp_buy[bybit] 18943/18943@0.00519694 slip=+3.7bps paper=+4.2 gap=-0.5bps reqs=5 sent=0
```

The gate refusal there is the missing certificate. In the backtest container
`LIVE_EXECUTION_ENABLED=true` with `BYBIT_TESTNET=true`.

To measure the gap once carries close, run on the shared DB (`context` is
`json`, so test with `->`, not `?`):
`SELECT context->'carry_exec_dry_run'->'open'->'gap_bps', context->'carry_exec_dry_run'->'close'->'legs'
FROM predictions WHERE strategy_id = 'neg_funding_carry' AND context->'carry_exec_dry_run' IS NOT NULL`.

## Open questions
- The simulated gap compares fills on the same book seconds apart. So it
  measures rounding, the band and the half-size, not latency or queue
  position. Only testnet or live fills measure those.
- ~~Live sizing is half of paper~~ — decided 2026-10-09: paper books carries
  against the combined notional (the conservative direction; no cap was
  loosened). Positions opened before the change run off at their old size.

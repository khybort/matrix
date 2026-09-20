---
title: Open questions
updated: 2026-09-19
status: current
---

What is genuinely unknown, phrased so a future session can close it. Ranked by
how much the answer would change.

## Claims
- ~~Does any signal in the book have edge before costs?~~ **Answered
  2026-09-20** by [[edge-study]] on all signals rather than fills:
  `momentum_xs` beats random entry by +36 bps (t=6.04, n=799); `grid`, `dca`
  and `funding_reversion` have none. The live question is now: **does that edge
  reach the wallet now that stale fills are blocked?** Same signal, same costs,
  only the latency removed — the first falsifiable profit hypothesis.
- **Is the horizon wrong, or the entry?** 57 % of exits are horizon exits at
  roughly minus cost — though part of that was fills arriving with only half
  the horizon left, which is now fixed. *Test*: for closed trades, measure max favourable and
  adverse excursion within the horizon. If MFE frequently exceeds the TP
  distance, the problem is exit timing; if not, the entry has no predictive
  content.
- **Can the EV ranker discriminate at all?** The counterfactual report says
  traded and skipped candidates perform about the same (−15.0 vs −13.9 bps,
  2026-09-13). *Test*: rank all candidates by EV, bucket into deciles, and plot
  realised PnL per decile. A flat curve means the ranker is decoration.
- **What is the true cost model?** Slippage is now measured per symbol from our
  own book snapshots ([[methods]]), but fees remain assumed and nothing is
  validated against real fills. Until live or exchange-simulated fills exist,
  every net-of-cost conclusion still carries that assumption.
- **Is the 15-second cadence justified?** Nothing has shown that faster
  decisions earn more than they cost in spread. *Test*: compare realised PnL of
  the same strategy at 15 s vs 60 s vs 300 s decision intervals.
- **Should BIST and US be running at all right now?** They add surface area,
  cost and attention while crypto has no edge. The argument for keeping them is
  regime diversification; there is no evidence yet either way.
- **Someone else holds this bot token.** ~~Why does one Telegram poller
  conflict with itself?~~ — answered 2026-09-20, and the answer is that it
  never was conflicting with itself. Measured from three vantage points with
  every local consumer stopped: a lone traced poller inside the container took
  3 conflicts in 240 s while its own in-flight request count never exceeded 1;
  plain host-side long polls returned 409 on 2 of 8; and no running container
  or host process held the token. Telegram hands each update to exactly one
  `getUpdates` caller, so the other consumer is not merely noisy — it can
  **receive the operator's commands instead of us**, and sending still works,
  which is why nothing looked broken. The token was created and pasted on
  2026-09-20; treat it as leaked. *Remedy, operator-only:* `/revoke` in
  BotFather, then `scripts/rotate_telegram_token.sh` (validates with getMe,
  writes `.env` without echoing, recreates notify, and reports whether the new
  token is clean). Until then `notify` counts conflicts, logs one line each
  instead of a traceback, and pushes an urgent alert hourly.

- **Uncommitted, live, and mine to land later:** the EV floor lets a
  *measured* edge outrank the model's own `confidence x tp_pct` estimate. The
  floor was rejecting 174 of 178 candidates an hour (2026-09-20), every
  momentum_xs signal among them, while that strategy measures +21 bps at the
  95% lower bound against a ~12 bps round trip. The change is written, tested
  (38/38) and running in the dev tree, but the EV floor itself is another
  author's work that has not reached git, so there is no HEAD to commit it
  against. Land it the moment their floor lands — until then it exists only in
  the working tree and would be lost by a hard reset.
- **Operator-blocked**: only `TELEGRAM_BOT_TOKEN` remains — without it every
  alert is invisible, which is what let the three-day outage pass unnoticed.
  The wallet refund was applied 2026-09-19 and lid sleep was disabled
  2026-09-20, so the system can finally accumulate an uninterrupted sample.

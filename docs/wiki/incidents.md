---
title: Incidents
updated: 2026-09-19
sources: [docs/CHANGES.md, git log, "~/.orbstack/log/vmgr.log", "pmset -g log"]
status: current
---

What has actually gone wrong, with the root cause and the fix. Most entries are
*silent* failures: the system looked healthy and produced nothing. That is the
dominant failure mode here, and the reason liveness is measured by output age
([[operations]]).

## Claims
- **2026-06-10 → 09-12, ~3 months down.** Nobody was running it. Recovered
  2026-09-12; everything before that date is historical.
- **2026-09-13 — challenger positions in the champion wallet.**
  `_open_for_market(shadow=True)` resolved the wallet a second time without the
  flag, so every challenger position was booked into the default wallet with no
  per-strategy slot cap: `funding_reversion` v3 did 1 198 trades and −$343 in
  one day. Fixed in 74eb6ff; the −346.76 refund is still pending
  ([[pnl-reality]]). All champion/challenger comparisons before this date are
  invalid.
- **2026-09-13 — a test truncated live `dev_tasks`.** The dev_agent conftest
  pointed at the live database; 9 task rows were lost and the live worker
  picked up a test task. Restored from the 15:35 backup; tests now use an
  isolated database.
- **2026-09-13 → 09-14, 11 hours down.** MacBook entered *Clamshell Sleep*; the
  OrbStack VM slept with it and the Docker socket hung. A kernel OOM in the
  10 GiB VM was a contributing (not root) cause. Fixes: per-container memory
  caps, VM raised to 12 GiB. The lid itself kept costing time (another 21 h on
  2026-09-19/20) until `sudo pmset -a disablesleep 1` was applied 2026-09-20.
- **2026-09-14 — the Claude credentials sync had never run.** launchd cannot
  execute a script under `~/Documents` (TCC, exit 126); the job looked
  installed. Moved to `~/.matrix/bin`, interval 30 min.
- **2026-09-14 — two daemons did not survive the host sleep.** The Bybit
  WebSocket stayed "open" and silent for 5 hours (`ping_interval=None`, no
  `ConnectionClosed`), and the bars tick hung. Fixes: reconnect after 60 s
  without a frame, bound each tick at 240 s.
- **2026-09-16 08:16 → 09-19 00:00, three days of no trading.** Postgres
  briefly reported "the database system is in recovery mode"; the paper
  engine's child process died on it. `watchfiles.run_process` only restarts on
  file changes, so the container stayed "Up" with nothing inside — 8 positions
  frozen, no opens, no closes, and no alert, because notify's detectors were
  also not reaching Telegram (token unset). Fixed 2026-09-19 by making the dev
  entrypoint a real supervisor (095b2c4).
- **Smaller silent bugs, same family**: `momentum_xs` stamped every version as
  v1 so reflection saw n=0 for the live ones; the bars aggregator walked a
  284 M-row index every minute; Director filed tasks into the wrong database
  tier; phantom slot rows diluted every real strategy's share to 3 slots
  instead of 8.

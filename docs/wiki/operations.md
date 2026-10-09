---
title: Operations
updated: 2026-10-09
sources: [Makefile, docs/OPS_HARDENING.md, scripts/, scripts/stall_watchdog.sh, services/ingestion/src/ingestion/carry_watchlist.py]
status: current
---

Running the system day to day. The host-level dependencies (lid sleep, VM
memory, launchd) are in the global wiki's machine page.

## Claims
- **Start / stop**: `make up-dev` (compose base + dev + limits overlays),
  `make down`, `make logs`, `make ps`, `make stats`. Single service:
  `docker compose ... up -d --no-deps <svc>` — without `--no-deps` compose
  recreates Postgres too.
- **Daily health, in one look**: age of the newest ticker snapshot, newest 1m
  bar, newest prediction, newest wallet snapshot, newest outcome. All five
  should be seconds-to-minutes old. Container state is *not* a health signal —
  see [[incidents]].
- **Money**: `make reset-capital ASSET=crypto [WALLET=default] [AMOUNT=+346.76]`
  adjusts wallet capital without touching learning data (`db-reset` truncates
  outcomes and must not be used for this). Used 2026-09-19 to refund
  +346.76 for the 2026-09-13 shadow-wallet bug ([[pnl-reality]]).
- **Risk**: `make circuit-reset ASSET=crypto`, Telegram `/circuit_reset`.
- **Learning**: `make method-ab`, `make ranker-ab`, `make leaderboard`,
  `make lab-scan`, `make director-once` / `director-tail` / `director-digest`.
- **Dev agent**: `make dev-agent-queue`, `make dev-agent-clean`, and from
  Telegram `/dev_tasks`, `/dev_accept <id>`, `/dev_discard <id>`,
  `/dev_revise <id> <notes>` — accept performs the real merge.
- **LLM**: `make llm-status`, `make claude-creds-sync`, `make claude-creds-install`.
  See [[llm-stack]].
- **Data**: `make retention-drain`, then `make db-compact TABLE=market_trades`
  to return disk to the OS. `make backup-now`; the sidecar dumps daily with
  14-day retention.
- **Knobs worth knowing**: `MATRIX_BACKLOG_SLOTS_MULT` (emission cap),
  `MATRIX_AGENT_UNIVERSE_CAP`, `MATRIX_AGENT_HOLD_COOLDOWN_S`,
  `MATRIX_AGENT_BATCH_CHUNK`, `MATRIX_LLM_DAILY_BUDGET_USD`,
  `MATRIX_LLM_CALL_TIMEOUT_S`, `MATRIX_SLOT_MIN_N`, `MATRIX_CHALLENGER_MODE`,
  `MATRIX_CERT_*`, `MATRIX_WATCH_SUPERVISE`.
- **Telegram**: configured since 2026-09-20 (rotated that day, see
  [[incidents]]); delivery verified 2026-10-09 from both paths — notify
  (`pushed … to 1/1 chats`) and the host watchdog (`make watchdog-test`).
  `OPENROUTER_API_KEY` is still unset.

## Host watchdog — the alert path that survives the VM

`make watchdog-install` loads two LaunchAgents: `com.matrix.caffeinate`
(`caffeinate -s`) and `com.matrix.watchdog`, which runs
`~/Library/Application Support/matrix/stall_watchdog.sh` every 5 minutes (a
copy — launchd cannot execute or read anything under `~/Documents`, so the two
Telegram keys are copied to `watchdog.env` there, mode 0600;
`scripts/rotate_telegram_token.sh` refreshes it). Re-run the install after
editing the script. Log: `~/Library/Logs/matrix-watchdog.log`; state:
`…/matrix/state/`. `make watchdog-status`, `make watchdog-test`.

It exists because notify lives inside the VM and dies with it: 2026-09-30 →
10-06 notify produced 783 alerts and delivered none ([[incidents]]). What the
host watchdog checks, alerting edge-triggered and queueing sends that fail:

| check | fires when | action |
|---|---|---|
| power | host on battery | alert every 30 min, every 5 min under 25 % |
| gap | watchdog did not run for 15 min+ | one post-hoc "down Xh" message, says if the host rebooted |
| docker | `docker info` fails for 30 s | starts OrbStack if not running, else alert |
| egress | 2 probes in a row: containers cannot reach Bybit/Telegram/Binance, host can | alert; `orb restart docker` only with `MATRIX_WATCHDOG_HEAL_EGRESS=1` |
| data | newest bybit BTCUSDT ticker > 30 min | alert (notify alerts at 5 min when it can) |
| disk | host free < 30 GiB | alert |
| stall | a loop service's last log line exceeds its budget | `docker restart <svc>` |

What no local check can do: report a host that is already off. The gap message
arrives only after it comes back, and after a power loss nothing runs until
someone logs in (automatic login is off; FileVault is off, so it can be enabled). Keep the machine on AC; the battery alert is
the only warning there will be.

## Crypto universe — what streams, what trades

Two sets since 2026-10-09 (`matrix_shared/markets/crypto.py`):

- **Traded** — `crypto_universe_async()`: `tradable_symbols` asset_class
  `crypto`, active (labs universe manager, ~25). The strategy dispatcher hands
  this to every crypto module; the agent trades it.
- **Streamed** — `crypto_ingest_universe_async()`: traded ∪ **carry
  watchlist** (`tradable_symbols` asset_class `crypto_carry`). Ingestion
  subscribes it, bars-aggregator aggregates it (`symbol = ANY(...)`).
  A module that wants watchlist coins must ask for `carry_watchlist_async()`
  itself — never widen the traded set, or directional modules trade coins
  chosen for their funding.

**Carry watchlist** (`services/ingestion/src/ingestion/carry_watchlist.py`,
runs inside `ingestion-market`): hourly at :40 (so a newcomer is subscribed
~20 min before the top-of-hour settlement `neg_funding_carry` reads), Bybit
USDT perps whose base coin is in the Bybit/Binance borrow tables and whose
min(last settled, live predicted) funding is ≤ −0.05 %; ≤ 20 by 24h turnover,
traded-set symbols excluded. Hysteresis: stays while ≤ −0.02 % or for 6 h
after it last qualified; incumbent turnover counts ×1.5 in the cap ranking.
Any symbol with an open `inverse_carry` prediction is pinned (the paper engine
accrues funding from the settlements the hold crosses). Row `components_json`
holds settled / predicted / borrow venue + rate / spot venue + pair. Knobs:
`CARRY_WATCHLIST_ENABLED`, `_N` (20), `_ENTER` (−0.0005), `_EXIT` (−0.0002),
`_KEEP_H` (6), `_REFRESH_MINUTE` (40). One refresh = ~370 public REST calls,
~10 s.

**Spot legs**: each member's spot pair streams from the cheapest-borrow
venue that lists it (else the other): Bybit spot (`exchange='bybit-spot'`) or
Binance spot (`'binance-spot'`, combined-stream WS). Ticker every 15 s, book
top-20/25 every 10 s, closed 1m candles into `market_bars` asset_class
**`crypto_spot`** (DEFAULT partition; a spot bar under `crypto` would collide
with the perp bar of the same name). Spot rows share the perp's symbol
(`KAIAUSDT`) except for scaled perps (`1000BTTUSDT` → `BTTUSDT`): any reader
of tickers/books without an `exchange` filter must not be pointed at a
watchlist coin — `symbol_costs` filters `exchange='bybit'`, and a coin that
joins the traded set loses its spot leg at the next 5-min reconcile.

Subscriptions change on the live socket (`set_symbols` → subscribe /
unsubscribe; Bybit spot caps a request at 10 args); nothing reconnects when
the set moves. bars-aggregator backfills 48 h of perp klines for a symbol that
joins mid-run (`BARS_JOIN_BACKFILL_HOURS`).

Check it:
```sql
-- shared: current watchlist
SELECT rank, symbol, score AS funding, liquidity_usd::bigint AS turnover,
       components_json->>'spot_venue' AS spot
FROM tradable_symbols WHERE asset_class = 'crypto_carry' AND active ORDER BY rank;
```
Logs: `carry watchlist: N active … added=[…] dropped=[…]` hourly;
`bybit linear: +[…] -[…]`, `binance spot: +[…]` on each change.

**Measured 2026-10-09 13:14–13:43 UTC** (18 watchlist perps + 18 spot legs,
on top of 24–25 traded symbols; baseline = the traded set over the hour
before):

| table | baseline rows/day | added rows/day | added bytes/day (heap+idx, est.) |
|---|---|---|---|
| `market_trades` | 13.6 M | +2.9 M (+22 %) | ~0.9 GB (7 d kept → ~6 GB) |
| `market_orderbook_snapshots` | 1.0 M | +0.78 M (perp 0.64 M, spot 0.14 M) | ~0.95 GB (2 d → ~2 GB) |
| `market_ticker_snapshots` | 0.44 M | +0.32 M (perp 0.24 M, spot 0.07 M) | ~75 MB (30 d → ~2.3 GB) |
| `market_bars` 1m | 34 k | +45 k (perp 22 k, spot 24 k) | ~15 MB (no retention) |

About +2 GB/day gross, ~+11 GB steady state against 190 GiB free; the 23 GB
of free space in the bloated L2 table absorbs it first (database grew 30 MB in
the first 36 min). CPU: `ingestion-market` 5.8 % → 6.7–9.3 % (docker stats
averages); Postgres was 36 % before and 52–75 % after, but two autovacuums
(`market_trades`, L2) were running in the after window, so that delta is not
the watchlist's. Retention keeps up: the time-ordered trades sweep and the
per-symbol L2/ticker sweeps all plan as index scans; per-symbol keys now
include `crypto_spot` bar symbols (spot pairs named unlike their perp would
otherwise never be pruned). A cold first ticker sweep took 49 s over 103
keys with nothing to delete (dead index entries), 0.6 s once warm.

Expected yield: replaying the holdout (2026-06-01 → 10-09, 1 105 executable
H1 episodes) through this selection (refresh at :40, cap 20, hysteresis,
predicted ≈ next settled) covers 1 103 of them — ~59/week, ~50/week over the
last 30 days — against ~1/week inside the traded set. Cap 10 covers 73 %,
cap 5 37 % (the cap keeps the liquid ones, which earn more).

Live check 2026-10-09 14:02: KAIAUSDT (1 h interval) settled at −0.50 %;
`NegFundingCarry(symbols=carry_watchlist_async()).generate()` run in the
strategy container drafted it, and the freshness guard kept all 19 watchlist
symbols (newest datum 0.3–5.5 s old).

**Open wiring (2026-10-09)**: the data streams, but the strategy dispatcher
still hands `neg_funding_carry` only the traded set, so the module never looks
at a watchlist coin. Until `strategy.main` gives it `crypto_symbols +
carry_watchlist_async()`, the yield above is potential, not booked.

## Shadow tracker — is the only bet working?

`matrix_shared/shadow_tracker.py` compares every shadow strategy that carries a
band against that band, in episodes (`edge_study.episode_groups`), never rows.
The band is config next to the strategy: `strategy_configs.params.shadow_band`
(seeded 2026-10-09 for `neg_funding_carry` v1; `DEFAULT_BANDS` in the module is
the fallback if a params rewrite drops it). Fields: `since`, `expected_bps_low/high`
(+100…+300), `floor_bps` (+30), `min_episodes` (20), `stale_hours` (72),
`min_qualifying` (10), `qualify_universe` (`crypto_carry`),
`qualify_funding_max` (−0.0008), `components` (funding, borrow, book).

Per closed episode: net bps = pnl / notional; book = `context.book_close.total_bps`;
borrow = `context.borrow_charged_usd`; funding = net + book + borrow (the paper
engine nets both costs out of pnl). Mean, median, CR1 t clustered by open day.

Verdicts, most severe first: **broken** (no episode opened for 72 h while the
watchlist had ≥ 10 settlements at ≤ −0.08 %; or a closed episode with zero
funding over ≥ 9 h, borrow not charged, or no book cost), **collecting**
(< 20 closed), **below_band** (mean ≤ +30: costs ate the edge, do not promote),
**on_track**. An unknown opportunity count never flags staleness.

Surfaces: one line per strategy in the Director brief
(`shadow neg_funding_carry/crypto: COLLECTING — 0/20 closed ep, 1 open · band
+100…+300, floor +30 · last open 1h ago · 2 qualifying settlements/72h`), and a
notify Telegram alert on a verdict change or a new broken reason, a persisting
`broken` again every 24 h (`MATRIX_SHADOW_REALERT_S`); evaluated every 15 min
(`MATRIX_SHADOW_TRACK_EVERY_S`). A first sighting alerts only for broken /
below_band; the last verdict is kept in `/tmp/notify_shadow_state.json` so a
watchfiles restart does not replay it. To track another shadow strategy, put a
`shadow_band` in its params.

State 2026-10-09 15:20 UTC: `collecting`, 1 open (KAIAUSDT), 0 closed; only 2
qualifying settlements in 72 h (both KAIA) on the 19-coin watchlist, far under
the ~21 per 72 h the 50/week expectation implies — staleness will not fire
below 10, so a quiet watchlist reads as collecting, not broken.

## Storage

The local database is the thing that will fill the disk, and `market_trades`
is nearly all of it. Retention lives in `matrix_shared/retention.py`, runs
inside `bars-aggregator` every 5 minutes, and keeps raw prints 7 days, L2
snapshots 2 days, ticker snapshots 30 days, wallet snapshots 30 days.

`market_trades` is swept globally in time order through `ix_market_trades_ts`;
the other tables are swept per symbol, which is right for them because they are
small. Check that retention is actually working, not merely running (2026-09-20
it ran for months and deleted almost nothing — see [[incidents]]):

```sql
-- The progress metric is the GLOBAL frontier, not one symbol's: the sweep
-- deletes oldest-first across all symbols, so a single symbol's minimum sits
-- still until its whole day is cleared. Cheap now that (trade_ts) is indexed.
SELECT min(trade_ts) FROM market_trades;   -- should be within the policy window

-- how much of the backlog is left
SELECT count(*) FROM market_trades WHERE trade_ts < now() - interval '7 days';
```

Deleting rows does not shrink the file. Space goes to Postgres' free-space map,
and only VACUUM puts it there, so autovacuum has to keep pace with retention or
the table extends on disk while rows disappear. `market_trades` carries
`autovacuum_vacuum_scale_factor = 0.02` (migration 0040) for exactly that
reason: the default 0.2 means waiting for ~64M dead tuples on a 322M-row table,
and autovacuum had never run on it at all.

```sql
-- has autovacuum ever touched it, and how far behind is it?
SELECT relname, n_dead_tup, last_autovacuum, autovacuum_count
FROM pg_stat_user_tables WHERE relname = 'market_trades';
```

**Deleting faster than VACUUM can reclaim makes the file grow, not shrink.**
A deleted row becomes a dead tuple and still occupies its page until VACUUM
returns it to the free-space map, so a drain that outruns autovacuum makes the
file grow instead of shrink. Measured on 2026-09-20, with the numbers stated
carefully because the first version of this note got them wrong by comparing
decimal GB against a GiB reading: the database went 112.0 → 116.0 GiB over two
hours, of which **3.4 GiB is the new `(trade_ts)` index**. Non-index growth was
0.5 GiB in two hours, about 6.5 GiB/day against a 1.56 GiB/day baseline — real,
but a fraction of the "12.5 GB in two hours" the bad arithmetic suggested.

The sustainable shape is a drain that outpaces ingestion by a few times, with
autovacuum given room to run:

| knob | value | why |
|---|---|---|
| `MATRIX_RETENTION_BUDGET_S` | 15 | ~20M rows/day, about 5x the inflow |
| `autovacuum_vacuum_scale_factor` | 0.02 | wakes at ~6.5M dead, not ~64M |

A full autovacuum pass on this table measured ~4 hours at the default throttle
and reclaims far more than a pass-worth of deletions creates, so the two
balance. Watch `pg_stat_progress_vacuum` and `pg_database_size` together — the
number that matters is whether the file has stopped growing, not how many rows
retention reports.

`make retention-drain` forces a full catch-up; `make db-compact TABLE=…` runs
VACUUM FULL to return space to the OS and **locks the table** while it does.

**Measured 2026-10-09 — the drain finished, the indexes did not shrink.**
`market_trades` is down to 18.3M rows (`reltuples`) and a **5.2 GB heap**
(from ~49 GB; vacuum truncated the emptied tail), oldest row 2026-09-30 00:16
— inside the policy window once the outage gap is counted. But its four
B-tree indexes are still **54 GiB** (exchange_id 29, pkey 13, symbol_ts 8.3,
ts 3.5): a B-tree only returns pages through REINDEX, so they are ten times
the size of the table they index, every autovacuum pass walks all of them,
and the feature path reads them through 128 MB of `shared_buffers`. Database
84.5 GiB (90.8e9 bytes, from 116 GiB on 09-20); VM volume 190 GiB free of 591;
host 198 GiB free. Runway is no longer a concern. The stats were reset by the
unclean shutdown, so `last_autovacuum` reads NULL — not evidence that it never
ran. Recommendation: `REINDEX INDEX CONCURRENTLY` each of the four, smallest
first (no write lock; needs a deliberate operator run — the session's
permission policy refused it as a shared-resource change).

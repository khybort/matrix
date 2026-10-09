Signal research 2026-10, round 2 (tick data) — reproducible scripts, host python3 + pandas/numpy.
Write-up: docs/wiki/signal-research-2026-10.md, section "Round 2 (ticks)". Pre-registration with log: PREREG.txt.
Needs round 1's data dir (../signal_2026_10/README.txt) as <r1>. <r2> is a scratch data dir.

  python3 -I panel.py <r1> <r2>/panel.txt                                  # 40-symbol tick panel
  python3 -I ticks2bars.py panel <r2>/panel.txt 2026-04-24 2026-10-08 <r2>/bars <r2>/tmp
        # streams ~6.7k Bybit archive files (~120 GB gz), keeps 1-minute bars only (~0.3 GB compressed)
  python3 -I fetch_binance.py <r2>/panel.txt <r2>/bn                       # H10
  python3 -I h9_events.py <r1> <r2>/h9_events.csv ; python3 -I fetch_5m.py <r2>/h9_events.csv <r2>/k5m.csv
  python3 -I spreadmap.py <r2>/spreadmap.json <r2>/bars [<other bars dirs>]
  python3 -I research_ticks.py counts|train <r2> <r1> ; ... holdout <r2> <r1> CELL...
  python3 -I research_klines.py counts|train <r2> <r1> ; ... holdout <r2> <r1> CELL...
  python3 -I fetch_early.py <r1> <r2>/early ; python3 -I research_klines.py early <r2> <r2>/early H11-flush-4h
  python3 -I by_table.py <r2>                                               # BY q-values over train cells

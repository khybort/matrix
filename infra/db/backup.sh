#!/bin/sh
# Scheduled logical backups for both Postgres tiers (runs in the postgres image).
#
#   backup.sh loop   — every BACKUP_INTERVAL_H hours (default 24), forever
#   backup.sh once   — one backup now, then exit
#
# Output: /backups/<UTC ts>/{local,shared}.dump (pg_dump custom format, -Z6).
# Raw market telemetry (BACKUP_EXCLUDE_DATA, default the three market_* stream
# tables) is dumped schema-only: it is replayable from the exchange and would
# otherwise be tens of GB. Everything that represents learned state — shared
# tier, graph, raw_documents — is included. Dumps older than BACKUP_KEEP_DAYS
# (default 14) are pruned.
#
# Restore: pg_restore -U matrix -d matrix --clean --if-exists local.dump
set -eu

OUT_ROOT=${BACKUP_DIR:-/backups}
INTERVAL_H=${BACKUP_INTERVAL_H:-24}
KEEP_DAYS=${BACKUP_KEEP_DAYS:-14}
LOCAL_HOST=${BACKUP_LOCAL_HOST:-postgres}
SHARED_HOST=${BACKUP_SHARED_HOST:-postgres-shared}
PGUSER=${PGUSER:-matrix}
export PGPASSWORD=${PGPASSWORD:-matrix_dev_only}
EXCLUDE=${BACKUP_EXCLUDE_DATA:-market_trades market_orderbook_snapshots market_ticker_snapshots}

exclude_args() {
  for t in $EXCLUDE; do printf -- '--exclude-table-data=%s ' "$t"; done
}

dump_one() {  # host db outfile
  if ! pg_isready -h "$1" -U "$PGUSER" -d "$2" >/dev/null 2>&1; then
    echo "  (skip $1/$2 — not reachable)"; return 0
  fi
  # shellcheck disable=SC2046
  pg_dump -h "$1" -U "$PGUSER" -d "$2" -Fc -Z6 $(exclude_args) -f "$3"
  echo "  ✓ $3 ($(du -h "$3" | cut -f1))"
}

run_once() {
  ts=$(date -u +%Y%m%d-%H%M%S)
  dir="$OUT_ROOT/$ts"
  mkdir -p "$dir"
  echo "→ backup $ts"
  dump_one "$LOCAL_HOST" matrix "$dir/local.dump"
  dump_one "$SHARED_HOST" matrix_shared "$dir/shared.dump"
  # prune
  find "$OUT_ROOT" -mindepth 1 -maxdepth 1 -type d -mtime +"$KEEP_DAYS" -exec rm -rf {} + 2>/dev/null || true
  echo "→ done; $(ls -1 "$OUT_ROOT" | wc -l | tr -d ' ') backup set(s) retained"
}

case "${1:-loop}" in
  once) run_once ;;
  loop)
    while true; do
      run_once || echo "backup failed (will retry next interval)"
      sleep $(( INTERVAL_H * 3600 ))
    done ;;
  *) echo "usage: backup.sh [once|loop]"; exit 2 ;;
esac

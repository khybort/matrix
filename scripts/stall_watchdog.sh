#!/usr/bin/env bash
# Matrix host watchdog — runs from launchd every 5 minutes (see
# infra/launchd/com.matrix.watchdog.plist, `make watchdog-install`).
#
# Two jobs:
#
# 1. Restart loop-services whose last log line is older than a per-service
#    silence budget. `restart: unless-stopped` only helps when a process EXITS;
#    after a host suspend these services keep the process alive while their
#    asyncio sleep never fires again.
#
# 2. Alert on Telegram FROM THE HOST when the machine itself is failing. The
#    in-container notify service cannot report these, because it shares their
#    fate: 2026-09-30 → 10-06 the VM lost all egress for six days and notify
#    logged 810 alerts it could not deliver; 2026-10-06 the laptop ran its
#    battery to zero and nothing said a word. Host-side checks:
#      power      — on battery (the host will die), back on AC
#      gap        — the watchdog itself did not run (host off / asleep / reboot)
#      docker     — engine unreachable; starts OrbStack if it is not running
#      egress     — containers cannot reach the internet while the host can
#      data       — newest BTCUSDT ticker snapshot too old
#      disk       — host free space low
#    Alerts are edge-triggered with a re-alert interval; a failed send is
#    retried next run, never silently dropped.
#
# Never destructive: the only actions are `docker restart <svc>`, `open -a
# OrbStack`, and — only if MATRIX_WATCHDOG_HEAL_EGRESS=1 — `orb restart docker`.
#
# Usage: scripts/stall_watchdog.sh [--dry-run] [--test-alert]
#   --dry-run     report what it would restart / send, change nothing
#   --test-alert  send one test message and exit (verifies the alert path)
set -uo pipefail

DRY_RUN=0
TEST_ALERT=0
for a in "$@"; do
  case "$a" in
    --dry-run) DRY_RUN=1 ;;
    --test-alert) TEST_ALERT=1 ;;
  esac
done

SUPPORT_DIR="${MATRIX_WATCHDOG_DIR:-$HOME/Library/Application Support/matrix}"
STATE_DIR="$SUPPORT_DIR/state"
ENV_FILE="$SUPPORT_DIR/watchdog.env"
mkdir -p "$STATE_DIR"

LOG_TAG="stall_watchdog"
log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) | ${LOG_TAG} | $*"; }

# The repo lives under ~/Documents, which launchd jobs cannot read (TCC), so
# `make watchdog-install` copies the two Telegram keys into ENV_FILE (0600).
# Parsed, not sourced: only these keys, nothing executes.
TELEGRAM_BOT_TOKEN=""
TELEGRAM_ALLOWED_CHAT_IDS=""
MATRIX_WATCHDOG_HEAL_EGRESS="${MATRIX_WATCHDOG_HEAL_EGRESS:-0}"
if [[ -r "$ENV_FILE" ]]; then
  while IFS='=' read -r k v; do
    v="${v%$'\r'}"; v="${v#\"}"; v="${v%\"}"
    case "$k" in
      TELEGRAM_BOT_TOKEN) TELEGRAM_BOT_TOKEN="$v" ;;
      TELEGRAM_ALLOWED_CHAT_IDS) TELEGRAM_ALLOWED_CHAT_IDS="$v" ;;
      MATRIX_WATCHDOG_HEAL_EGRESS) MATRIX_WATCHDOG_HEAL_EGRESS="$v" ;;
    esac
  done < <(grep -E '^[A-Z_]+=' "$ENV_FILE")
fi

# Thresholds (seconds / GiB).
GAP_S=900                 # watchdog runs every 300 s; 3 missed runs = host was down
DATA_STALE_S=1800         # notify alerts at 300 s when it can; this is the backstop
EGRESS_FAILS_TO_ALERT=2   # consecutive failed probes (≈10 min)
EGRESS_FAILS_TO_HEAL=6    # ≈30 min, only with MATRIX_WATCHDOG_HEAL_EGRESS=1
HEAL_COOLDOWN_S=7200
DISK_MIN_GIB=30
REALERT_S=3600

NOW="$(date -u +%s)"

# No `timeout` on macOS. alarm() survives exec, so the child gets SIGALRM.
# Every docker call goes through this: a hung socket must not hang the run
# (launchd will not start the next one while this one is alive).
with_timeout() { perl -e 'alarm shift @ARGV; exec @ARGV' "$@"; }

utc() { date -u -r "$1" +"%m-%d %H:%M"; }
fmt_dur() { local s=$1; if (( s >= 3600 )); then echo "$((s / 3600))h$(((s % 3600) / 60))m"; else echo "$((s / 60))m"; fi; }

# --- Telegram ---------------------------------------------------------------
# The token goes to curl on stdin (-K -), never in argv, so it is not visible
# in `ps` and never reaches the log.
tg_send() {
  local text="$1" id code sent=0
  [[ -n "$TELEGRAM_BOT_TOKEN" && -n "$TELEGRAM_ALLOWED_CHAT_IDS" ]] || { log "telegram not configured ($ENV_FILE): $text"; return 1; }
  if (( DRY_RUN )); then log "WOULD send: $text"; return 0; fi
  for id in ${TELEGRAM_ALLOWED_CHAT_IDS//,/ }; do
    code="$(printf 'url = "https://api.telegram.org/bot%s/sendMessage"\n' "$TELEGRAM_BOT_TOKEN" \
      | curl -s -m 15 -o /dev/null -w '%{http_code}' -K - \
          --data-urlencode "chat_id=$id" --data-urlencode "text=[host] $text" 2>/dev/null)"
    [[ "$code" == "200" ]] && sent=$((sent + 1)) || log "telegram send failed (http=${code:-none}) chat=$id"
  done
  (( sent > 0 ))
}

# Point events that must not be lost: queue on failure, flush every run.
event() {
  tg_send "$1" && return 0
  (( DRY_RUN )) && return 0
  printf '%s\n' "$1" > "$STATE_DIR/pending.$NOW.$RANDOM"
}
flush_pending() {
  local f
  for f in "$STATE_DIR"/pending.*; do
    [[ -e "$f" ]] || continue
    tg_send "$(cat "$f") (delayed; queued $(utc "$(stat -f %m "$f")") UTC)" && rm -f "$f"
  done
}

# Stateful conditions: alert on entry, re-alert every $3 s, recover message on exit.
alert() {
  local key="$1" text="$2" every="${3:-$REALERT_S}" last=0
  [[ -f "$STATE_DIR/alert.$key" ]] && last="$(cat "$STATE_DIR/alert.$key")"
  (( DRY_RUN )) || touch "$STATE_DIR/active.$key"
  (( NOW - last < every )) && return 0
  log "ALERT $key: $text"
  tg_send "$text" && ! (( DRY_RUN )) && echo "$NOW" > "$STATE_DIR/alert.$key"
  return 0
}
clear_alert() {
  local key="$1" text="$2"
  [[ -f "$STATE_DIR/active.$key" ]] || return 0
  log "RECOVERED $key: $text"
  if tg_send "Recovered: $text" || (( DRY_RUN )); then
    (( DRY_RUN )) || rm -f "$STATE_DIR/active.$key" "$STATE_DIR/alert.$key"
  fi
}

counter() { local f="$STATE_DIR/count.$1"; [[ -f "$f" ]] && cat "$f" || echo 0; }
set_counter() { (( DRY_RUN )) || echo "$2" > "$STATE_DIR/count.$1"; }

# --- host checks ------------------------------------------------------------
check_gap() {
  local last=0 boot
  [[ -f "$STATE_DIR/last_run" ]] && last="$(cat "$STATE_DIR/last_run")"
  boot="$(sysctl -n kern.boottime | sed -E 's/^\{ sec = ([0-9]+),.*/\1/')"
  (( DRY_RUN )) || echo "$NOW" > "$STATE_DIR/last_run"
  (( last > 0 && NOW - last > GAP_S )) || return 0
  local why="host was asleep or the watchdog was not loaded"
  (( boot > last )) && why="host REBOOTED at $(utc "$boot") UTC — power loss or crash; nothing runs again until someone logs in"
  event "Matrix was down $(fmt_dur $((NOW - last))): watchdog silent $(utc "$last") → $(utc "$NOW") UTC; $why."
}

check_power() {
  local batt src pct remain
  batt="$(pmset -g batt 2>/dev/null)" || return 0
  src="$(head -1 <<<"$batt")"
  pct="$(grep -oE '[0-9]+%' <<<"$batt" | head -1)"
  remain="$(grep -oE '[0-9]+:[0-9]+ remaining' <<<"$batt" | head -1)"
  if [[ "$src" == *"Battery Power"* ]]; then
    local every=1800 n="${pct%\%}"
    [[ -n "$n" ]] && (( n <= 25 )) && every=300
    alert power "Host is on BATTERY (${pct:-?}, ${remain:-unknown}). When it reaches 0 the VM, Postgres and every loop die (2026-10-06: 2.9 days lost). Plug in AC." "$every"
  else
    clear_alert power "host back on AC power (${pct:-?})."
  fi
}

check_disk() {
  local free_gib
  free_gib="$(df -k /System/Volumes/Data 2>/dev/null | awk 'NR==2 {print int($4 / 1048576)}')"
  [[ -n "$free_gib" ]] || return 0
  if (( free_gib < DISK_MIN_GIB )); then
    alert disk "Host disk free ${free_gib} GiB (< ${DISK_MIN_GIB}). The OrbStack data image grows into it; Postgres panics on a full disk."
  else
    clear_alert disk "host disk free ${free_gib} GiB."
  fi
}

docker_ok() { with_timeout 30 docker info >/dev/null 2>&1; }

check_docker() {
  if docker_ok; then
    clear_alert docker "Docker engine reachable."
    return 0
  fi
  if ! pgrep -xq OrbStack; then
    if (( DRY_RUN )); then log "WOULD start OrbStack"; else log "OrbStack not running; starting it"; open -ga OrbStack; fi
    alert docker "Docker unreachable and OrbStack was not running — started it. Containers come back via restart policy; check again in 5 min."
  else
    alert docker "Docker unreachable for 30 s+ while OrbStack runs — the VM is hung (seen 2026-09-13 after a VM OOM). Operator: \`orb restart\`."
  fi
  return 1
}

# Can a container reach the internet? Probe from the ingestion container (it
# is the one that needs egress); fall back to notify.
container_egress_ok() {
  local c
  for c in matrix-ingestion-market matrix-notify; do
    docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$c" || continue
    with_timeout 40 docker exec "$c" sh -c 'p=$(ls /workspace/services/*/.venv/bin/python 2>/dev/null | head -1); "${p:-python3}" -c "
import socket
ok = 0
for h in (\"api.bybit.com\", \"api.telegram.org\", \"api.binance.com\"):
    try:
        socket.create_connection((h, 443), 8).close(); ok += 1
    except OSError:
        pass
raise SystemExit(0 if ok else 1)"' >/dev/null 2>&1
    return $?
  done
  return 0  # nothing to probe from; the stall/data checks cover a dead stack
}
host_egress_ok() { curl -s -m 10 -o /dev/null https://api.telegram.org; }

check_egress() {
  if container_egress_ok; then
    set_counter egress 0
    clear_alert egress "containers can reach the internet again."
    return 0
  fi
  if ! host_egress_ok; then
    log "host has no internet either; nothing to alert through"
    return 0
  fi
  local n; n=$(( $(counter egress) + 1 )); set_counter egress "$n"
  (( n >= EGRESS_FAILS_TO_ALERT )) || return 0
  alert egress "Containers have NO internet while the host does ($n probes, ~$((n * 5)) min). Market data, LLM and Telegram from inside are all dead (2026-09-30: this lasted 6 days, 810 alerts undelivered). Operator: \`orb restart docker\`, or set MATRIX_WATCHDOG_HEAL_EGRESS=1 in $ENV_FILE."
  if [[ "$MATRIX_WATCHDOG_HEAL_EGRESS" == "1" ]] && (( n >= EGRESS_FAILS_TO_HEAL )); then
    local last_heal=0
    [[ -f "$STATE_DIR/last_heal" ]] && last_heal="$(cat "$STATE_DIR/last_heal")"
    if (( NOW - last_heal >= HEAL_COOLDOWN_S )); then
      if (( DRY_RUN )); then log "WOULD orb restart docker"; else
        echo "$NOW" > "$STATE_DIR/last_heal"
        event "Self-heal: restarting the OrbStack docker engine after $n failed egress probes (graceful container stop, data volumes untouched)."
        with_timeout 300 orb restart docker >/dev/null 2>&1 || log "orb restart docker failed or timed out"
      fi
    fi
  fi
}

check_data() {
  local age
  age="$(with_timeout 30 docker exec matrix-postgres psql -U matrix -d matrix -Atc \
    "SELECT extract(epoch FROM now() - snapshot_ts)::int FROM market_ticker_snapshots WHERE symbol = 'BTCUSDT' AND exchange = 'bybit' ORDER BY snapshot_ts DESC LIMIT 1" 2>/dev/null)"
  [[ "$age" =~ ^-?[0-9]+$ ]] || return 0
  if (( age > DATA_STALE_S )); then
    alert data "Market data stale: newest BTCUSDT ticker is $(fmt_dur "$age") old. Strategies are trading on frozen prices or not at all."
  else
    clear_alert data "market data fresh (BTCUSDT ticker ${age}s old)."
  fi
}

# --- stall restarts (job 1) -------------------------------------------------
# Per-service max tolerated silence in seconds. Request-driven services
# (brain, backtest-api, web, notify) legitimately stay silent when idle.
service_budget() {
  case "$1" in
    matrix-agent)             echo 600  ;;
    matrix-strategy)          echo 900  ;;
    matrix-backtest)          echo 1800 ;;
    matrix-reflection)        echo 2400 ;;
    matrix-labs)              echo 600  ;;
    matrix-agent-lessons)     echo 7800 ;;  # hourly synth loop
    matrix-synthesis)         echo 7800 ;;  # hourly synth loop
    matrix-graph)             echo 1800 ;;
    matrix-bars-aggregator)   echo 1200 ;;
    matrix-ingestion-market)  echo 1200 ;;
    matrix-ingestion-news)    echo 5400 ;;
    matrix-execution)         echo 1800 ;;
    *)                        echo 0    ;;  # 0 = not watched
  esac
}

# Epoch seconds of a container's most recent log line (0 if unknown).
last_log_epoch() {
  local c="$1" ts
  ts="$(with_timeout 20 docker logs -t --tail 1 "$c" 2>&1 | tail -1 | cut -d' ' -f1)"
  ts="${ts%%.*}"
  ts="${ts%Z}"
  [[ -z "$ts" ]] && { echo 0; return; }
  date -j -u -f "%Y-%m-%dT%H:%M:%S" "$ts" +%s 2>/dev/null || echo 0
}

restart_stalled() {
  local c restarted=0
  while read -r c; do
    [[ -z "$c" ]] && continue
    local budget; budget="$(service_budget "$c")"
    [[ "$budget" -eq 0 ]] && continue
    local last; last="$(last_log_epoch "$c")"
    [[ "$last" -eq 0 ]] && continue     # can't read time; skip rather than flap
    local age=$(( NOW - last ))
    if (( age > budget )); then
      if (( DRY_RUN )); then
        log "WOULD restart $c (silent ${age}s > ${budget}s)"
      else
        log "restarting $c (silent ${age}s > ${budget}s)"
        with_timeout 120 docker restart "$c" >/dev/null 2>&1 && restarted=$((restarted + 1)) \
          || log "restart FAILED for $c"
      fi
    fi
  done < <(with_timeout 30 docker ps --format '{{.Names}}' 2>/dev/null | grep '^matrix-' || true)
  (( restarted > 0 )) && log "done: restarted ${restarted} stalled service(s)"
  return 0
}

main() {
  if (( TEST_ALERT )); then
    tg_send "watchdog test from $(hostname -s) at $(utc "$NOW") UTC — the host-side alert path works." \
      && { log "test alert delivered"; exit 0; } || { log "test alert FAILED"; exit 1; }
  fi
  command -v docker >/dev/null 2>&1 || { log "docker not on PATH; abort"; exit 0; }
  check_gap
  flush_pending
  check_power
  check_disk
  if check_docker; then
    check_egress
    check_data
    restart_stalled
  fi
  exit 0
}

main

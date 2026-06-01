#!/usr/bin/env bash
# Update DuckDNS A record to this machine's current public IPv4.
#
# In .env (gitignored):
#   DUCKDNS_SUBDOMAIN=matrix
#   DUCKDNS_TOKEN=<from https://www.duckdns.org>
#
# Cron (every 5 min): */5 * * * * cd /path/to/matrix && ./scripts/duckdns-update.sh -q
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

SUB="${DUCKDNS_SUBDOMAIN:-}"
TOKEN="${DUCKDNS_TOKEN:-}"
QUIET=0
for arg in "$@"; do
  [[ "$arg" == "-q" ]] && QUIET=1
done

if [[ -z "$SUB" || -z "$TOKEN" ]]; then
  echo "Set DUCKDNS_SUBDOMAIN and DUCKDNS_TOKEN in .env (see .env.example)" >&2
  exit 1
fi

IP="$(curl -4 -fsS --max-time 15 https://api.ipify.org || curl -4 -fsS --max-time 15 https://ifconfig.me/ip)"
IP="${IP//$'\r'/}"
IP="${IP//$'\n'/}"

RESP="$(curl -fsS --max-time 15 "https://www.duckdns.org/update?domains=${SUB}&token=${TOKEN}&ip=${IP}")"
if [[ "$RESP" != "OK" ]]; then
  echo "DuckDNS update failed: $RESP" >&2
  exit 1
fi

URL="http://${SUB}.duckdns.org:3030"
if [[ "$QUIET" -eq 0 ]]; then
  echo "DuckDNS OK → ${IP} (${URL})"
else
  echo "${URL}"
fi

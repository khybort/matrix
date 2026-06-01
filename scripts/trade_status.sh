#!/usr/bin/env bash
# Print Bybit trading mode from .env (no secrets).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${ROOT}/.env"

read_env() {
  local key="$1" default="${2:-}"
  if [[ -f "$ENV_FILE" ]]; then
    local line
    line="$(grep -E "^${key}=" "$ENV_FILE" | tail -1 || true)"
    if [[ -n "$line" ]]; then
      echo "${line#*=}"
      return
    fi
  fi
  echo "$default"
}

testnet="$(read_env BYBIT_TESTNET true)"
live="$(read_env LIVE_EXECUTION_ENABLED false)"
cap="$(read_env LIVE_CAPITAL_CAP_USD 2000)"
tn_key="$(read_env BYBIT_TESTNET_API_KEY)"
mn_key="$(read_env BYBIT_API_KEY)"

testnet_lc="$(printf '%s' "$testnet" | tr '[:upper:]' '[:lower:]')"
live_lc="$(printf '%s' "$live" | tr '[:upper:]' '[:lower:]')"

if [[ "$testnet_lc" == "false" ]]; then
  network="mainnet (real money)"
  creds="mainnet keys"
  if [[ -n "$mn_key" ]]; then creds+=": set"; else creds+=": missing"; fi
else
  network="testnet (sandbox)"
  creds="testnet keys"
  if [[ -n "$tn_key" ]]; then creds+=": set"; else creds+=": missing"; fi
fi

if [[ "$live_lc" == "true" ]]; then
  mode="LIVE — orders may hit Bybit (cert + risk gates still apply)"
else
  mode="PAPER/DRY-RUN — no exchange orders (LIVE_EXECUTION_ENABLED=false)"
fi

echo "Bybit network:     $network"
echo "Execution mode:    $mode"
echo "Credentials:       $creds"
echo "Capital cap USD:   $cap"
echo ""
echo "To enable real Bybit orders on testnet:"
echo "  1. BYBIT_TESTNET=true"
echo "  2. BYBIT_TESTNET_API_KEY + BYBIT_TESTNET_API_SECRET in .env"
echo "  3. LIVE_EXECUTION_ENABLED=true  (+ paper_trade_certificate per strategy)"
echo ""
echo "Mainnet (real money): BYBIT_TESTNET=false + BYBIT_API_KEY/SECRET + LIVE_EXECUTION_ENABLED=true"

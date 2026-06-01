#!/usr/bin/env bash
# Toggle the Matrix LLM backend + default tier by rewriting keys in .env.
#
# Backend axis (subscription <-> bedrock <-> cursor) and tier axis (haiku <-> sonnet)
# are orthogonal: switching one preserves the other. After any switch, restart the
# stack so containers re-read .env:  make down && make up-dev   (or: make restart)
#
# Secrets (AWS creds, optional CURSOR_API_KEY) are written to .env only — never echoed.
# .env is gitignored.
#
# Usage: scripts/llm_backend.sh <bedrock|subscription|cursor|haiku|sonnet|status>
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT/.env}"

PROFILE="${BEDROCK_AWS_PROFILE:-efsora-admin}"
REGION="${BEDROCK_AWS_REGION:-us-east-1}"
# Bedrock cross-region inference-profile IDs. Override via env if your account
# exposes different versions. Verify with: aws bedrock list-inference-profiles
# Verified ACTIVE against efsora-admin / us-east-1 (aws bedrock list-inference-profiles, 2026-05-29).
BEDROCK_HAIKU="${BEDROCK_HAIKU:-us.anthropic.claude-haiku-4-5-20251001-v1:0}"
BEDROCK_SONNET="${BEDROCK_SONNET:-us.anthropic.claude-sonnet-4-6}"
BEDROCK_OPUS="${BEDROCK_OPUS:-us.anthropic.claude-opus-4-7}"

upsert_env() { # key value  — replace existing `key=` line (move to end) or append
  local key="$1" val="$2" tmp
  tmp="$(mktemp "$(dirname "$ENV_FILE")/.env.tmp.XXXXXX")"
  [ -f "$ENV_FILE" ] && grep -v "^${key}=" "$ENV_FILE" > "$tmp" || true
  printf '%s=%s\n' "$key" "$val" >> "$tmp"
  mv "$tmp" "$ENV_FILE"
}

get_env() { grep "^$1=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- || true; }

cmd_subscription() {
  # Clear alternate backends; empty values fall back to subscription auth
  # (CLAUDE_CODE_OAUTH_TOKEN, left untouched) and Anthropic short-name models.
  upsert_env MATRIX_LLM_BACKEND ""
  upsert_env CLAUDE_CODE_USE_BEDROCK ""
  upsert_env AWS_ACCESS_KEY_ID ""
  upsert_env AWS_SECRET_ACCESS_KEY ""
  upsert_env AWS_SESSION_TOKEN ""
  upsert_env AWS_REGION ""
  upsert_env MATRIX_MODEL_HAIKU ""
  upsert_env MATRIX_MODEL_SONNET ""
  upsert_env MATRIX_MODEL_OPUS ""
  echo "✓ LLM backend → Claude Code subscription (CLAUDE_CODE_OAUTH_TOKEN)"
  if [ -z "$(get_env CLAUDE_CODE_OAUTH_TOKEN)" ]; then
    echo "⚠ CLAUDE_CODE_OAUTH_TOKEN is empty in .env — LLM path will be disabled."
  fi
  echo "  Restart to apply:  make down && make up-dev"
}

cmd_bedrock() {
  command -v aws >/dev/null || { echo "✗ aws CLI not found on host"; exit 1; }
  upsert_env MATRIX_LLM_BACKEND ""
  local creds
  if ! creds="$(aws configure export-credentials --profile "$PROFILE" --format env-no-export 2>/dev/null)"; then
    echo "✗ Could not export credentials for profile '$PROFILE'."
    echo "  SSO session likely expired. Run this, then retry:"
    echo "      aws sso login --profile $PROFILE"
    exit 1
  fi
  local akid asak atok
  akid="$(printf '%s\n' "$creds" | grep '^AWS_ACCESS_KEY_ID='     | cut -d= -f2-)"
  asak="$(printf '%s\n' "$creds" | grep '^AWS_SECRET_ACCESS_KEY=' | cut -d= -f2-)"
  atok="$(printf '%s\n' "$creds" | grep '^AWS_SESSION_TOKEN='     | cut -d= -f2- || true)"
  [ -n "$akid" ] && [ -n "$asak" ] || { echo "✗ Empty creds from export"; exit 1; }

  upsert_env CLAUDE_CODE_USE_BEDROCK "1"
  upsert_env AWS_REGION "$REGION"
  upsert_env AWS_ACCESS_KEY_ID "$akid"
  upsert_env AWS_SECRET_ACCESS_KEY "$asak"
  upsert_env AWS_SESSION_TOKEN "$atok"
  upsert_env MATRIX_MODEL_HAIKU "$BEDROCK_HAIKU"
  upsert_env MATRIX_MODEL_SONNET "$BEDROCK_SONNET"
  upsert_env MATRIX_MODEL_OPUS "$BEDROCK_OPUS"
  echo "✓ LLM backend → AWS Bedrock (profile=$PROFILE, region=$REGION)"
  echo "  Models: haiku=$BEDROCK_HAIKU"
  echo "          sonnet=$BEDROCK_SONNET"
  echo "  Note: SSO creds are temporary — re-run 'make llm-bedrock' when they expire."
  echo "  Verify model access: aws bedrock list-inference-profiles --region $REGION"
  echo "  Restart to apply:  make down && make up-dev"
}

cmd_tier() { # haiku|sonnet
  upsert_env MATRIX_DEFAULT_MODEL "$1"
  echo "✓ Default LLM tier → $1 (decision override, lessons feeder, bulletin)"
  echo "  graph/synthesis/reflection stay pinned to Sonnet regardless."
  echo "  Restart to apply:  make down && make up-dev"
}

_cursor_cli_logged_in() {
  command -v cursor >/dev/null 2>&1 || return 1
  local out
  out="$(cursor agent status 2>&1)" || return 1
  case "$out" in
    *"Not logged in"*|*"Authentication required"*) return 1 ;;
    *) return 0 ;;
  esac
}

cmd_cursor() {
  upsert_env MATRIX_LLM_BACKEND "cursor"
  upsert_env MATRIX_CURSOR_MODEL "auto"
  upsert_env CLAUDE_CODE_USE_BEDROCK ""
  upsert_env AWS_ACCESS_KEY_ID ""
  upsert_env AWS_SECRET_ACCESS_KEY ""
  upsert_env AWS_SESSION_TOKEN ""
  upsert_env AWS_REGION ""
  upsert_env MATRIX_MODEL_HAIKU ""
  upsert_env MATRIX_MODEL_SONNET ""
  upsert_env MATRIX_MODEL_OPUS ""
  echo "✓ LLM backend → Cursor Auto (MATRIX_LLM_BACKEND=cursor, model=auto)"
  if [ -n "$(get_env CURSOR_API_KEY)" ]; then
    echo "  Auth: CURSOR_API_KEY set (optional API-key override)"
  elif _cursor_cli_logged_in; then
    echo "  Auth: cursor agent login (subscription-style CLI session)"
  else
    echo "⚠ Not authenticated — host login does not reach Docker."
    echo "      make cursor-login-docker   # once per machine (persists in volume)"
    echo "  Or set CURSOR_API_KEY in .env (Dashboard → Integrations) for CI/Docker."
  fi
  echo "  Restart to apply:  make down && make up-dev"
}

cmd_status() {
  local be tier hk sn region keyhint cursor_keyhint
  be="$(get_env MATRIX_LLM_BACKEND)"
  tier="$(get_env MATRIX_DEFAULT_MODEL)"
  hk="$(get_env MATRIX_MODEL_HAIKU)"
  sn="$(get_env MATRIX_MODEL_SONNET)"
  region="$(get_env AWS_REGION)"
  keyhint="$(get_env AWS_ACCESS_KEY_ID | cut -c1-6)"
  cursor_keyhint="$(get_env CURSOR_API_KEY | cut -c1-8)"
  if [ "$be" = "cursor" ]; then
    cursor_model="$(get_env MATRIX_CURSOR_MODEL)"
    if [ -n "$(get_env CURSOR_API_KEY)" ]; then
      echo "Backend       : cursor-auto (api_key=${cursor_keyhint}…, model=${cursor_model:-auto})"
    elif _cursor_cli_logged_in; then
      echo "Backend       : cursor-auto (cli-login, model=${cursor_model:-auto})"
    else
      echo "Backend       : cursor-auto (not authenticated, model=${cursor_model:-auto})"
    fi
  elif [ -n "$(get_env CLAUDE_CODE_USE_BEDROCK)" ]; then
    echo "Backend       : bedrock (region=$region, key=${keyhint}…)"
  else
    echo "Backend       : subscription"
  fi
  echo "Default tier  : ${tier:-sonnet (default)}"
  echo "Haiku model   : ${hk:-claude-haiku-4-5-20251001 (subscription default)}"
  echo "Sonnet model  : ${sn:-claude-sonnet-4-6 (subscription default)}"
}

case "${1:-}" in
  subscription) cmd_subscription ;;
  bedrock)      cmd_bedrock ;;
  cursor)       cmd_cursor ;;
  haiku)        cmd_tier haiku ;;
  sonnet)       cmd_tier sonnet ;;
  status)       cmd_status ;;
  *) echo "usage: $0 <bedrock|subscription|cursor|haiku|sonnet|status>"; exit 2 ;;
esac

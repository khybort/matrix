#!/usr/bin/env bash
# Sync the host's Claude Code subscription login into the containers.
#
# `claude setup-token` long-lived tokens came back 401 on this account, so the
# containers instead reuse the HOST's claude.ai OAuth session: the access token
# is read from the macOS keychain ("Claude Code-credentials") and written to the
# shared docker volume `matrix_claude_config`, mounted at /root/.claude in every
# LLM container. The refresh token is deliberately STRIPPED — if a container
# refreshed it, the rotation would invalidate the host's login. Instead this
# script runs hourly (launchd, see infra/launchd/com.matrix.claude-creds.plist)
# and, when the access token has < 90 min left, first pokes the host CLI so it
# refreshes its own credentials, then copies the fresh token.
#
# Usage: scripts/claude_creds_sync.sh          (idempotent; safe to cron)
set -euo pipefail

VOLUME="${MATRIX_CLAUDE_VOLUME:-matrix_claude_config}"
MIN_LEFT_MIN="${MATRIX_CLAUDE_MIN_LEFT_MIN:-90}"

read_creds() { security find-generic-password -s "Claude Code-credentials" -w 2>/dev/null || true; }

minutes_left() {
  python3 -c 'import json,sys,time; d=json.loads(sys.stdin.read() or "{}"); e=d.get("claudeAiOauth",{}).get("expiresAt",0); print(int((e/1000-time.time())/60) if e else -1)'
}

raw="$(read_creds)"
if [ -z "$raw" ]; then
  echo "✗ no Claude Code credentials in keychain — run \`claude\` once on the host and log in" >&2
  exit 1
fi
left="$(printf '%s' "$raw" | minutes_left)"
if [ "$left" -lt "$MIN_LEFT_MIN" ]; then
  echo "→ access token has ${left} min left; poking host CLI to refresh"
  env -u CLAUDE_CODE_OAUTH_TOKEN claude -p "ok" --model claude-haiku-4-5-20251001 --output-format json >/dev/null 2>&1 || true
  raw="$(read_creds)"
  left="$(printf '%s' "$raw" | minutes_left)"
fi

# Strip the refresh token so containers can never rotate the host's session.
stripped="$(printf '%s' "$raw" | python3 -c '
import json,sys
d=json.loads(sys.stdin.read()); oa=d.get("claudeAiOauth",{})
oa.pop("refreshToken", None); oa.pop("refreshTokenExpiresAt", None)
print(json.dumps({"claudeAiOauth": oa}))')"

docker volume create "$VOLUME" >/dev/null
printf '%s' "$stripped" | docker run --rm -i -v "$VOLUME:/c" alpine:3.20 sh -c \
  'cat > /c/.credentials.json && chmod 600 /c/.credentials.json && echo "  ✓ /root/.claude/.credentials.json updated in volume"'
echo "→ container Claude access token valid for ~${left} min (resynced hourly)"

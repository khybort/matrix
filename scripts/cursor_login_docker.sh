#!/usr/bin/env bash
# Authenticate Cursor CLI inside Matrix LLM containers (Linux).
#
# Host `cursor agent login` (macOS) does NOT propagate into Docker. Copying
# ~/.cursor/cli-config.json is insufficient — session tokens are bound to the
# container login flow. Use this script once per machine (state persists on
# volume matrix_cursor_agent → /root/.cursor and matrix_cursor_config →
# /root/.config/cursor (auth.json lives here).
#
# Alternatives:
#   - Set CURSOR_API_KEY in .env (Dashboard → Integrations), then make up-dev
#   - Interactive:  make cursor-login-docker
#   - URL-only wait: CURSOR_LOGIN_WAIT=1 make cursor-login-docker
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

CONTAINER="${MATRIX_CURSOR_LOGIN_CONTAINER:-matrix-agent}"
WAIT_MODE="${CURSOR_LOGIN_WAIT:-0}"
POLL_S="${CURSOR_LOGIN_POLL_S:-300}"

if ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
  echo "Container $CONTAINER not found. Start stack: make up-dev" >&2
  exit 1
fi

if ! docker exec "$CONTAINER" test -x /root/.local/bin/cursor 2>/dev/null; then
  echo "Cursor CLI missing in $CONTAINER. Rebuild LLM images:" >&2
  echo "  make build SVC=agent graph brain synthesis reflection" >&2
  exit 1
fi

echo "Cursor Docker login → $CONTAINER (volume matrix_cursor_agent:/root/.cursor)"
echo ""

if [ -n "${CURSOR_API_KEY:-}" ] || grep -qE '^CURSOR_API_KEY=.+' .env 2>/dev/null; then
  echo "CURSOR_API_KEY is set — CLI login optional (cursor_enabled uses API key)."
  docker exec "$CONTAINER" cursor agent status 2>/dev/null || true
  exit 0
fi

status_out() {
  docker exec "$CONTAINER" cursor agent status 2>&1 || true
}

# "Not logged in" contains the substring "logged in" — never grep for that alone.
cursor_is_logged_in() {
  case "$1" in
    *"Not logged in"*|*"Authentication required"*) return 1 ;;
    *"Logged in"*) return 0 ;;
    *) return 1 ;;
  esac
}

status="$(status_out)"
if cursor_is_logged_in "$status"; then
  echo "Already authenticated in container:"
  echo "$status"
  exit 0
fi

if [ "$WAIT_MODE" != "1" ]; then
  echo "Open an interactive login in the container (browser OAuth):"
  echo "  docker exec -it -e NO_OPEN_BROWSER=1 $CONTAINER cursor agent login"
  echo ""
  echo "When the URL appears, complete sign-in in your host browser."
  echo "Then verify: docker exec $CONTAINER cursor agent status"
  echo ""
  exec docker exec -it -e NO_OPEN_BROWSER=1 "$CONTAINER" cursor agent login
fi

# Non-interactive helper: print URL, poll until logged in or timeout.
docker exec "$CONTAINER" rm -f /tmp/cursor-login.log 2>/dev/null || true
docker exec -d "$CONTAINER" sh -c \
  'NO_OPEN_BROWSER=1 cursor agent login > /tmp/cursor-login.log 2>&1'

sleep 2
if docker exec "$CONTAINER" test -f /tmp/cursor-login.log 2>/dev/null; then
  docker exec "$CONTAINER" cat /tmp/cursor-login.log 2>/dev/null || true
fi

url="$(docker exec "$CONTAINER" sh -c \
  "grep -o 'https://cursor.com/loginDeepControl[^[:space:]]*' /tmp/cursor-login.log 2>/dev/null | head -1" || true)"

echo ""
if [ -n "$url" ]; then
  echo "Complete login in your browser (same Google account as host):"
  echo "  $url"
  echo ""
  if command -v open >/dev/null 2>&1; then
    open "$url" || true
  fi
else
  echo "Waiting for login URL in container log…"
fi

echo "Polling cursor agent status (up to ${POLL_S}s)…"
deadline=$((SECONDS + POLL_S))
while [ "$SECONDS" -lt "$deadline" ]; do
  status="$(status_out)"
  if cursor_is_logged_in "$status"; then
    echo ""
    echo "$status"
    echo "✓ Container authenticated (shared by graph/synthesis/reflection/dev_agent via volume)."
    exit 0
  fi
  sleep 3
done

echo "Timed out — login not completed." >&2
echo "Run interactively: make cursor-login-docker" >&2
exit 1

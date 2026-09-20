#!/usr/bin/env bash
# Swap the Telegram bot token after a BotFather /revoke.
#
# Why this exists: a bot token delivers each update to exactly ONE getUpdates
# caller. On 2026-09-20 a second consumer was measured holding this bot's token
# (host-side long polls returned 409 with every local consumer stopped), which
# both spams the log and steals the operator's commands. Revoking in BotFather
# is the only fix, and the new token has to reach .env without ever being
# echoed, logged, or committed.
#
# Usage:
#   scripts/rotate_telegram_token.sh              # prompts, input hidden
#   NEW_TOKEN=... scripts/rotate_telegram_token.sh   # non-interactive
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -n "${NEW_TOKEN:-}" ]; then
  tok="$NEW_TOKEN"
else
  printf 'BotFather /revoke sonrası YENİ token (ekrana yazılmaz): ' >&2
  read -rs tok
  printf '\n' >&2
fi
tok="$(printf '%s' "$tok" | tr -d '[:space:]')"
[ -n "$tok" ] || { echo "empty token, aborting" >&2; exit 1; }

# Validate before touching .env — a bad token must not take the channel down.
who=$(curl -s --max-time 15 "https://api.telegram.org/bot${tok}/getMe" \
      | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['result']['username'] if d.get('ok') else '')")
[ -n "$who" ] || { echo "token rejected by Telegram (getMe failed); .env untouched" >&2; exit 1; }
echo "token valid, bot is @${who}"

python3 - "$tok" <<'PY'
import re, sys, pathlib
tok = sys.argv[1]
p = pathlib.Path(".env"); s = p.read_text()
line = f"TELEGRAM_BOT_TOKEN={tok}"
s, n = re.subn(r'^TELEGRAM_BOT_TOKEN=.*$', line, s, flags=re.M)
if not n:
    s = s.rstrip("\n") + "\n" + line + "\n"
p.write_text(s)
print("wrote .env (gitignored)")
PY

docker compose -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.limits.yml \
  up -d --no-deps --force-recreate notify >/dev/null 2>&1 || docker compose up -d --no-deps --force-recreate notify >/dev/null 2>&1
echo "notify recreated; watching 90s for getUpdates conflicts..."
sleep 90
n=$(docker compose logs --since 2m notify 2>/dev/null | grep -c 'getUpdates conflict' || true)
if [ "$n" = "0" ]; then
  echo "RESULT: clean — no conflicts in 90s, the token is ours alone again."
else
  echo "RESULT: $n conflict(s) still — the new token is ALSO shared, or the old poller cached it."
fi

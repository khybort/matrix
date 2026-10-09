#!/usr/bin/env bash
# Run every Python test suite inside its service's docker image, then the web
# typecheck, and print a per-suite pass/fail summary.
#
#   scripts/test_all.sh                 # everything
#   scripts/test_all.sh shared execution # only the named suites
#   PYTEST_ARGS="-x -k carry" scripts/test_all.sh strategy
#   SKIP_WEB=1 scripts/test_all.sh
#
# Suite names: service directory names under services/ plus `shared`
# (packages/python-shared) and `web` (apps/web typecheck).
#
# Why docker: the host has no uv; each image carries its service's locked venv.
# `--entrypoint uv` because the image entrypoint is `uv run python -m`.
# The service's src, tests and pyproject.toml are bind-mounted so the run sees
# the working tree (pytest config included) without a rebuild.
#
# The backtest suite shares postgres-shared with the live paper engine, which
# expires the fixtures' predictions under the tests' feet. The live `backtest`
# container is stopped for that suite and ALWAYS restarted (trap), even on
# Ctrl-C or a failed run.
set -o pipefail  # no -u: macOS bash 3.2 treats empty arrays as unbound

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

DC=(docker compose -f docker-compose.yml -f docker-compose.dev.yml)
PYTEST_ARGS=${PYTEST_ARGS:-}

ALL_SUITES=(shared agent agent_lessons backtest brain dev_agent director execution
            graph ingestion labs notify reflection strategy synthesis bulletin web)

# service dir -> compose service that owns the image
compose_service() {
  case "$1" in
    shared) echo backtest ;;            # any image with matrix_shared + pytest + numpy
    agent_lessons) echo agent-lessons ;;
    ingestion) echo ingestion-market ;;
    *) echo "$1" ;;
  esac
}

BACKTEST_STOPPED=0
restart_backtest() {
  if [ "$BACKTEST_STOPPED" = 1 ]; then
    echo ">> restarting live backtest engine"
    "${DC[@]}" start backtest >/dev/null 2>&1 || echo "!! failed to restart backtest — run: docker compose start backtest"
    BACKTEST_STOPPED=0
  fi
}
trap restart_backtest EXIT
trap 'restart_backtest; exit 130' INT TERM

run_py_suite() {
  local suite=$1 svc dir mounts=() extra=() with=()
  svc=$(compose_service "$suite")
  if [ "$suite" = shared ]; then
    dir=/workspace/packages/python-shared
    mounts+=(-v "$ROOT/packages/python-shared/tests:$dir/tests"
             -v "$ROOT/packages/python-shared/pyproject.toml:$dir/pyproject.toml:ro")
  else
    dir=/workspace/services/$suite
    mounts+=(-v "$ROOT/services/$suite/src:$dir/src"
             -v "$ROOT/services/$suite/tests:$dir/tests"
             -v "$ROOT/services/$suite/pyproject.toml:$dir/pyproject.toml:ro")
  fi
  mounts+=(-v "$ROOT/packages/python-shared/src:/workspace/packages/python-shared/src")
  case "$suite" in
    # path deps imported by these services
    agent|labs|synthesis) mounts+=(-v "$ROOT/services/graph/src:/workspace/services/graph/src") ;;
  esac
  case "$suite" in
    labs) mounts+=(-v "$ROOT/services/agent/src:/workspace/services/agent/src") ;;
  esac
  # ingestion's image ships without the dev group
  [ "$suite" = ingestion ] && with=(--with pytest --with pytest-asyncio) && extra=(-o asyncio_mode=auto)
  # dev_agent's conftest defaults to localhost; inside the network the DB is `postgres`
  # (it redirects to the isolated <db>_devagent_test database itself).
  local envs=(-e DEV_AGENT_TEST_DSN=postgres://matrix:matrix_dev_only@postgres:5432/matrix)

  local image
  image=$("${DC[@]}" --profile phase6 config --format json 2>/dev/null \
          | python3 -c "import json,sys; print(json.load(sys.stdin)['services'].get('$svc',{}).get('image',''))")
  if [ -z "$image" ] || ! docker image inspect "$image" >/dev/null 2>&1; then
    echo "SKIP (no image ${image:-for $svc}; make build)"; return 77
  fi

  if [ "$suite" = backtest ] && [ -n "$("${DC[@]}" ps -q backtest 2>/dev/null)" ]; then
    echo ">> stopping live backtest engine for the suite"
    "${DC[@]}" stop backtest >/dev/null && BACKTEST_STOPPED=1
  fi

  # shellcheck disable=SC2086
  "${DC[@]}" --profile phase6 run --rm --no-deps -T "${mounts[@]}" "${envs[@]}" -w "$dir" \
    --entrypoint uv "$svc" run --no-sync "${with[@]}" pytest -q -rfE --disable-warnings -p no:cacheprovider "${extra[@]}" $PYTEST_ARGS
  local rc=$?
  [ "$suite" = backtest ] && restart_backtest
  return $rc
}

run_web() {
  docker image inspect matrix-web:local >/dev/null 2>&1 || { echo "SKIP (no matrix-web image)"; return 77; }
  # The dev-stage image carries node_modules; the dev overlay mounts src + tsconfig.
  # `next typegen` first: route-handler signature errors live in .next/types only.
  "${DC[@]}" run --rm --no-deps -T -w /app/apps/web --entrypoint sh web -c 'pnpm exec next typegen >/dev/null && pnpm exec tsc --noEmit'
}

suites=("$@")
[ ${#suites[@]} -eq 0 ] && suites=("${ALL_SUITES[@]}")
[ "${SKIP_WEB:-0}" = 1 ] && suites=("${suites[@]/web}")

declare -a SUMMARY
fail=0
LOG_DIR=${LOG_DIR:-$(mktemp -d -t matrix-tests.XXXXXX)}
for s in "${suites[@]}"; do
  [ -z "$s" ] && continue
  echo "================ $s ================"
  log="$LOG_DIR/$s.log"
  if [ "$s" = web ]; then run_web 2>&1 | tee "$log"; rc=${PIPESTATUS[0]}
  else run_py_suite "$s" 2>&1 | tee "$log"; rc=${PIPESTATUS[0]}; fi
  tail_line=$(grep -E "(passed|failed|error|no tests ran)" "$log" | tail -1)
  case $rc in
    0) SUMMARY+=("PASS  $s  $tail_line") ;;
    77) SUMMARY+=("SKIP  $s  $(grep '^SKIP' "$log" | head -1)") ;;
    *) SUMMARY+=("FAIL  $s  (rc=$rc) $tail_line"); fail=1 ;;
  esac
done

echo
echo "================ summary (logs: $LOG_DIR) ================"
printf '%s\n' "${SUMMARY[@]}"
exit $fail

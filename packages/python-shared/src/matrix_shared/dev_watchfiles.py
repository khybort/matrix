"""Dev compose entrypoint: debounced file watcher **plus a crash supervisor**.

Two jobs:

1. Hot reload — restart the child when a watched ``.py`` changes. The stock
   ``watchfiles`` CLI does not expose ``debounce`` (default 1600ms) or a
   startup grace window; rapid saves / format-on-save / multi-path watches
   caused restart storms and OrbStack CPU spikes.
2. **Keep the child alive.** ``watchfiles.run_process`` only restarts on file
   changes: when the child exits by itself the container stays "Up" with no
   worker inside, silently. On 2026-09-16 the paper engine died on a transient
   ``the database system is in recovery mode`` and the system did not trade for
   three days while every container looked healthy. The supervisor below
   restarts a child that exits, with capped backoff, and logs each restart.

Env:
  MATRIX_WATCH_DEBOUNCE_MS — debounce window (default 2500)
  MATRIX_WATCH_GRACE_S     — ignore changes N seconds after each start (default 2)
  MATRIX_WATCH_RESTART_MIN_S / _MAX_S — crash-restart backoff (default 2 / 60)
  MATRIX_WATCH_SUPERVISE   — "0" disables the crash supervisor (reload only)
"""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import sys
import threading
import time

from watchfiles import watch
from watchfiles.filters import PythonFilter

_DEBOUNCE_MS = int(os.environ.get("MATRIX_WATCH_DEBOUNCE_MS", "2500"))
_GRACE_S = float(os.environ.get("MATRIX_WATCH_GRACE_S", "2"))
_RESTART_MIN_S = float(os.environ.get("MATRIX_WATCH_RESTART_MIN_S", "2"))
_RESTART_MAX_S = float(os.environ.get("MATRIX_WATCH_RESTART_MAX_S", "60"))
_SUPERVISE = os.environ.get("MATRIX_WATCH_SUPERVISE", "1").strip().lower() not in ("0", "false")
_STOP_GRACE_S = float(os.environ.get("MATRIX_WATCH_STOP_GRACE_S", "5"))
_IGNORE_DIR_NAMES = frozenset({"tests", "test", ".pytest_cache", ".ruff_cache", ".mypy_cache"})


class MatrixDevFilter(PythonFilter):
    """Python sources only; skip test trees and tool caches under watch roots."""

    def __call__(self, change, path: str) -> bool:
        if not super().__call__(change, path):
            return False
        parts = path.replace("\\", "/").split("/")
        return not any(part in _IGNORE_DIR_NAMES for part in parts)


def next_backoff(current: float, *, min_s: float = _RESTART_MIN_S, max_s: float = _RESTART_MAX_S) -> float:
    """Exponential backoff for crash restarts, capped so a hard-failing child
    still retries once a minute (a DB in recovery comes back)."""
    return min(max(current * 2, min_s), max_s)


def _log(msg: str) -> None:
    print(f"[dev-supervisor] {msg}", file=sys.stderr, flush=True)


def _spawn(argv: list[str]) -> subprocess.Popen:
    return subprocess.Popen(argv)


def _stop(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=_STOP_GRACE_S)
    except subprocess.TimeoutExpired:
        _log("child did not stop on SIGTERM; killing")
        proc.kill()
        proc.wait(timeout=_STOP_GRACE_S)


def main() -> None:
    if len(sys.argv) < 3:
        print(
            "usage: python -m matrix_shared.dev_watchfiles '<shell command>' <watch-path> [...]",
            file=sys.stderr,
        )
        sys.exit(2)
    argv = shlex.split(sys.argv[1])
    paths = sys.argv[2:]

    stop = threading.Event()
    reload_requested = threading.Event()

    def _handle_signal(*_: object) -> None:
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _handle_signal)

    def _watch_loop() -> None:
        try:
            for _changes in watch(
                *paths,
                watch_filter=MatrixDevFilter(),
                debounce=_DEBOUNCE_MS,
                stop_event=stop,
                rust_timeout=1000,
                yield_on_timeout=False,
            ):
                reload_requested.set()
        except Exception as e:  # noqa: BLE001 — a broken watcher must not kill the child
            _log(f"file watcher stopped ({e}); running without hot reload")

    threading.Thread(target=_watch_loop, name="dev-watch", daemon=True).start()

    backoff = 0.0
    proc = _spawn(argv)
    started_at = time.monotonic()
    _log(f"started: {' '.join(argv)}")

    while not stop.is_set():
        time.sleep(0.5)

        if reload_requested.is_set():
            reload_requested.clear()
            if time.monotonic() - started_at < _GRACE_S:
                continue
            _log("change detected; restarting")
            _stop(proc)
            proc = _spawn(argv)
            started_at = time.monotonic()
            backoff = 0.0
            continue

        rc = proc.poll()
        if rc is None:
            continue
        if not _SUPERVISE:
            _log(f"child exited rc={rc}; supervisor disabled, exiting")
            sys.exit(rc)
        backoff = next_backoff(backoff)
        _log(f"child exited rc={rc} after {time.monotonic() - started_at:.0f}s; restarting in {backoff:.0f}s")
        if stop.wait(backoff):
            break
        proc = _spawn(argv)
        started_at = time.monotonic()

    _stop(proc)


if __name__ == "__main__":
    main()

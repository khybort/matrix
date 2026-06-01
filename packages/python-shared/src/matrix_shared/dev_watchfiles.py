"""Dev compose entrypoint: debounced watchfiles with extra ignores.

The stock ``watchfiles`` CLI does not expose ``debounce`` (default 1600ms) or a
startup grace window. Rapid saves / format-on-save / multi-path watches (e.g.
labs + agent + python-shared) caused restart storms and OrbStack CPU spikes.

Env:
  MATRIX_WATCH_DEBOUNCE_MS — debounce window (default 2500)
  MATRIX_WATCH_GRACE_S     — ignore changes N seconds after each start (default 2)
"""

from __future__ import annotations

import os
import sys

from watchfiles import run_process
from watchfiles.filters import PythonFilter

_DEBOUNCE_MS = int(os.environ.get("MATRIX_WATCH_DEBOUNCE_MS", "2500"))
_GRACE_S = float(os.environ.get("MATRIX_WATCH_GRACE_S", "2"))
_IGNORE_DIR_NAMES = frozenset({"tests", "test", ".pytest_cache", ".ruff_cache", ".mypy_cache"})


class MatrixDevFilter(PythonFilter):
    """Python sources only; skip test trees and tool caches under watch roots."""

    def __call__(self, change, path: str) -> bool:
        if not super().__call__(change, path):
            return False
        parts = path.replace("\\", "/").split("/")
        return not any(part in _IGNORE_DIR_NAMES for part in parts)


def main() -> None:
    if len(sys.argv) < 3:
        print(
            "usage: python -m matrix_shared.dev_watchfiles '<shell command>' <watch-path> [...]",
            file=sys.stderr,
        )
        sys.exit(2)
    target = sys.argv[1]
    paths = sys.argv[2:]
    run_process(
        *paths,
        target=target,
        target_type="command",
        watch_filter=MatrixDevFilter(),
        debounce=_DEBOUNCE_MS,
        grace_period=_GRACE_S,
    )


if __name__ == "__main__":
    main()

"""The dev entrypoint must restart a child that exits on its own — a container
whose worker died silently cost three days of trading on 2026-09-16."""

from __future__ import annotations

import subprocess
import sys
import textwrap
import time
from pathlib import Path

from matrix_shared.dev_watchfiles import next_backoff


def test_backoff_grows_and_caps():
    b = next_backoff(0.0, min_s=2, max_s=60)
    assert b == 2
    seq = []
    for _ in range(8):
        b = next_backoff(b, min_s=2, max_s=60)
        seq.append(b)
    assert seq[0] == 4 and max(seq) == 60 and seq == sorted(seq)


def test_supervisor_restarts_a_crashing_child(tmp_path: Path):
    counter = tmp_path / "runs.txt"
    child = tmp_path / "child.py"
    child.write_text(textwrap.dedent(f"""
        from pathlib import Path
        p = Path({str(counter)!r})
        p.write_text(str(int(p.read_text() or 0) + 1) if p.exists() else "1")
        raise SystemExit(1)
    """))
    watch_dir = tmp_path / "src"
    watch_dir.mkdir()
    env = {"MATRIX_WATCH_RESTART_MIN_S": "0.2", "MATRIX_WATCH_RESTART_MAX_S": "0.2", "PATH": "/usr/bin:/bin"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "matrix_shared.dev_watchfiles",
         f"{sys.executable} {child}", str(watch_dir)],
        env={**env, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 15
        runs = 0
        while time.monotonic() < deadline:
            if counter.exists():
                runs = int(counter.read_text() or 0)
                if runs >= 3:
                    break
            time.sleep(0.2)
        assert runs >= 3, f"child restarted only {runs} time(s); supervisor is not restarting crashes"
    finally:
        proc.terminate()
        proc.wait(timeout=10)

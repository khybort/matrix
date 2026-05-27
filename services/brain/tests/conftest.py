"""Make the brain source importable for host-side pure-logic tests.

These tests cover the dependency-light modules (tier routing, SSE framing,
history formatting, the deny-hook) without FastAPI/asyncpg/SDK. DB- and
SDK-backed behavior is verified live against the running stack.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

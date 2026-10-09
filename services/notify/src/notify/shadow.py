"""Shadow-book verdict alerts (matrix_shared.shadow_tracker).

Tells the operator, without being asked, whether a shadow bet is earning what
its research predicted. Alerts when a strategy's verdict changes, and repeats
a persisting `broken` at most once per `SHADOW_REALERT_S`. A first sighting
(fresh process, new band) alerts only for `broken` / `below_band`, so a
restart does not replay "collecting".

The last verdict per strategy is kept in a small JSON file: watchfiles
restarts the process on every shared-package save, and an in-memory map
would re-send `broken` on each of them.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from matrix_shared.shadow_tracker import BELOW_BAND, BROKEN, format_alert

from notify.health import ALERT_INFO, ALERT_WARNING

SHADOW_EVERY_S = float(os.environ.get("MATRIX_SHADOW_TRACK_EVERY_S", "900"))
SHADOW_REALERT_S = float(os.environ.get("MATRIX_SHADOW_REALERT_S", "86400"))
STATE_PATH = Path(os.environ.get("MATRIX_SHADOW_STATE_PATH", "/tmp/notify_shadow_state.json"))

_LOUD = (BROKEN, BELOW_BAND)


def _sig(rep: dict[str, Any]) -> str:
    """Verdict plus broken reasons: a new failure mode under `broken` is news."""
    return rep["verdict"] + ("|" + ",".join(rep["reasons"]) if rep["reasons"] else "")


def detect_shadow_alerts(
    prev: dict[str, dict[str, Any]],
    reports: list[dict[str, Any]],
    now: datetime,
    *,
    realert_s: float = SHADOW_REALERT_S,
) -> tuple[list[tuple[str, str]], dict[str, dict[str, Any]]]:
    """Pure: (alerts, new_state). State: key → {sig, verdict, alerted_at (epoch s)}."""
    alerts: list[tuple[str, str]] = []
    state = dict(prev)
    ts = now.timestamp()
    for rep in reports:
        key = f"{rep['strategy_id']}/{rep['asset_class']}"
        sig, v = _sig(rep), rep["verdict"]
        old = prev.get(key)
        if old is None:
            fire = v in _LOUD
        elif old["sig"] != sig:
            fire = True
        else:
            fire = v == BROKEN and ts - float(old.get("alerted_at") or 0) >= realert_s
        if fire:
            level = ALERT_WARNING if v in _LOUD else ALERT_INFO
            alerts.append((level, format_alert(rep, old["verdict"] if old else None)))
        state[key] = {
            "sig": sig, "verdict": v,
            "alerted_at": ts if fire else (old or {}).get("alerted_at", 0.0),
        }
    return alerts, state


def load_state(path: Path = STATE_PATH) -> dict[str, dict[str, Any]]:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def save_state(state: dict[str, dict[str, Any]], path: Path = STATE_PATH) -> None:
    try:
        path.write_text(json.dumps(state))
    except OSError as e:
        logger.warning(f"shadow tracker state not saved: {e}")

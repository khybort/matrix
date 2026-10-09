"""Shadow-book verdict alerts (matrix_shared.shadow_tracker).

Tells the operator, without being asked, whether a shadow bet is earning what
its research predicted. Alerts when a strategy's verdict changes, and repeats
a persisting `broken` at most once per `SHADOW_REALERT_S`. A first sighting
(fresh process, new band) alerts only for `broken` / `below_band`, so a
restart does not replay "collecting".

`review_due` is sent once per strategy, the first time its closed episodes
with `borrow_source=series` reach the band's revisit threshold: it states
which pre-registered revisit rule holds and the env change it implies. The
env is the main session's to change; nothing here applies it.

An alert counts as sent only once Telegram has taken it: `detect_shadow_alerts`
leaves the strategy's state untouched for every alert it emits, and
`mark_delivered` applies the alert's update after `push` confirms delivery.
An undelivered alert therefore fires again on the next shadow tick (the
2026-09-30 egress outage left notify unable to deliver 783 alerts for nine
days; a once-only `review_due` lost that way would never have come back).

The state lives in the local DB (`notify_alert_state`, key `shadow`):
watchfiles restarts the process on every shared-package save and a container
recreate wipes /tmp, and either used to re-send `broken` or lose `review_sent`.
"""

from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple

from loguru import logger
from sqlalchemy import text

from matrix_shared import local_session_scope
from matrix_shared.shadow_tracker import BELOW_BAND, BROKEN, format_alert, format_review

from notify.health import ALERT_INFO, ALERT_WARNING

SHADOW_EVERY_S = float(os.environ.get("MATRIX_SHADOW_TRACK_EVERY_S", "900"))
SHADOW_REALERT_S = float(os.environ.get("MATRIX_SHADOW_REALERT_S", "86400"))
STATE_KEY = "shadow"
# Where the state lived before notify_alert_state; read once to seed the table.
LEGACY_STATE_PATH = Path("/tmp/notify_shadow_state.json")

_LOUD = (BROKEN, BELOW_BAND)


def _sig(rep: dict[str, Any]) -> str:
    """Verdict plus broken reasons: a new failure mode under `broken` is news."""
    return rep["verdict"] + ("|" + ",".join(rep["reasons"]) if rep["reasons"] else "")


class ShadowAlert(NamedTuple):
    level: str
    text: str
    key: str
    update: dict[str, Any]  # merged into state[key] once delivered


def detect_shadow_alerts(
    prev: dict[str, dict[str, Any]],
    reports: list[dict[str, Any]],
    now: datetime,
    *,
    realert_s: float = SHADOW_REALERT_S,
) -> tuple[list[ShadowAlert], dict[str, dict[str, Any]]]:
    """Pure: (alerts, new_state). State: key → {sig, verdict, alerted_at (epoch s), review_sent}.

    A verdict that fires leaves its key's sig / verdict / alerted_at as they
    were, and `review_due` leaves `review_sent` False: `mark_delivered` moves
    them once Telegram has the alert. A silent change is applied at once."""
    alerts: list[ShadowAlert] = []
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
        verdict = {"sig": sig, "verdict": v, "alerted_at": ts if fire else (old or {}).get("alerted_at", 0.0)}
        if fire:
            level = ALERT_WARNING if v in _LOUD else ALERT_INFO
            alerts.append(ShadowAlert(level, format_alert(rep, old["verdict"] if old else None), key, verdict))
        review_sent = bool((old or {}).get("review_sent"))
        if (rep.get("revisit") or {}).get("due") and not review_sent:
            alerts.append(ShadowAlert(ALERT_WARNING, format_review(rep), key, {"review_sent": True}))
        if not fire:
            state[key] = {**verdict, "review_sent": review_sent}
    return alerts, state


def mark_delivered(state: dict[str, dict[str, Any]], alert: ShadowAlert) -> None:
    """Record a delivered alert in `state` (in place). A first-sighting alert
    delivered without its key in state yet creates the entry."""
    cur = state.get(alert.key) or {"sig": None, "verdict": None, "alerted_at": 0.0, "review_sent": False}
    state[alert.key] = {**cur, **alert.update}


async def deliver_shadow_alerts(
    state: dict[str, dict[str, Any]],
    alerts: list[ShadowAlert],
    send: Callable[[str, str], Awaitable[bool]],
) -> int:
    """Send each alert; mark it in `state` only when `send` confirms delivery.
    Returns how many were delivered."""
    n = 0
    for a in alerts:
        if await send(a.level, a.text):
            mark_delivered(state, a)
            n += 1
        else:
            logger.warning(f"shadow alert for {a.key} not delivered; it fires again next shadow tick")
    return n


async def load_state(key: str = STATE_KEY) -> dict[str, dict[str, Any]] | None:
    """The saved state; {} when none was ever saved (seeded from the legacy
    /tmp file if it is still there); None when the DB cannot be read: the
    caller must not evaluate, or every loud verdict would re-fire."""
    try:
        async with local_session_scope() as s:
            raw = (await s.execute(
                text("SELECT state FROM notify_alert_state WHERE key = :k"), {"k": key}
            )).scalar_one_or_none()
    except Exception as e:  # noqa: BLE001 — retried on the next tick
        logger.warning(f"shadow tracker state not loaded: {e}")
        return None
    if raw is None:
        if key == STATE_KEY:
            try:
                return json.loads(LEGACY_STATE_PATH.read_text())
            except (OSError, ValueError):
                pass
        return {}
    return raw if isinstance(raw, dict) else json.loads(raw)


async def save_state(state: dict[str, dict[str, Any]], key: str = STATE_KEY) -> bool:
    try:
        async with local_session_scope() as s:
            await s.execute(
                text(
                    "INSERT INTO notify_alert_state (key, state, updated_at) "
                    "VALUES (:k, CAST(:s AS jsonb), now()) "
                    "ON CONFLICT (key) DO UPDATE SET state = EXCLUDED.state, updated_at = now()"
                ),
                {"k": key, "s": json.dumps(state)},
            )
        return True
    except Exception as e:  # noqa: BLE001 — the in-memory state carries on; saved next tick
        logger.warning(f"shadow tracker state not saved: {e}")
        return False

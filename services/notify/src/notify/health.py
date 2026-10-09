"""Liveness / degradation detectors that work across containers.

The LLM breaker, ingestion loops and paper engine each live in their own
process, so notify cannot read their in-memory state. Everything here is
derived from what those processes *write*: table freshness, prediction
context, dev_task rows, disk usage. That makes the detectors restart-safe
and true for every node that shares the DB.

`collect_health()` does the I/O; `detect_health_alerts()` is pure and
edge-triggered (alert on entering a bad state, re-alert every
`REALERT_S` while it persists, INFO on recovery) so a 60s poll doesn't
spam Telegram.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import text

from matrix_shared import local_session_scope, shared_session_scope

ALERT_URGENT = "URGENT"
ALERT_WARNING = "WARNING"
ALERT_INFO = "INFO"

# Thresholds (seconds unless noted). Env-tunable for slow nodes.
PAPER_ENGINE_STALE_S = int(os.environ.get("MATRIX_HEALTH_PAPER_STALE_S", "180"))
SIGNAL_STALE_S = int(os.environ.get("MATRIX_HEALTH_SIGNAL_STALE_S", "900"))
INGEST_STALE_S = int(os.environ.get("MATRIX_HEALTH_INGEST_STALE_S", "300"))
BARS_STALE_S = int(os.environ.get("MATRIX_HEALTH_BARS_STALE_S", "1500"))
# No paper position opened anywhere for this long = the system is up but not
# trading. 2026-09-29 → 10-09 nothing filled for ten days (slots at 0, EV floor
# rejecting every candidate, then no data) and no detector said so, because
# every other freshness signal — predictions, snapshots, ticks — stayed green.
FILL_STALE_S = int(os.environ.get("MATRIX_HEALTH_FILL_STALE_S", "86400"))
DEV_TASK_HEARTBEAT_STALE_S = int(os.environ.get("MATRIX_HEALTH_DEV_HEARTBEAT_STALE_S", "600"))
DISK_FREE_MIN_PCT = float(os.environ.get("MATRIX_HEALTH_DISK_FREE_MIN_PCT", "15"))
LLM_WINDOW_MIN = int(os.environ.get("MATRIX_HEALTH_LLM_WINDOW_MIN", "60"))
LLM_DAILY_BUDGET_USD = float(os.environ.get("MATRIX_LLM_DAILY_BUDGET_USD", "25"))
REALERT_S = int(os.environ.get("MATRIX_HEALTH_REALERT_S", "3600"))


@dataclass(slots=True)
class HealthSample:
    """One tick's worth of freshness facts. `None` = could not measure."""

    now: datetime
    paper_snapshot_age_s: float | None = None
    paper_fill_age_s: float | None = None   # newest paper_positions.opened_at
    crypto_prediction_age_s: float | None = None
    crypto_tick_age_s: float | None = None  # newest ticker snapshot (raw stream)
    crypto_bar_age_s: float | None = None   # newest 1m bar (bars-aggregator)
    disk_free_pct: float | None = None
    # LLM path: predictions in the window from matrix_agent, split by method.
    llm_configured: bool = False
    agent_predictions_in_window: int = 0
    agent_llm_predictions_in_window: int = 0
    # LLM spend today (matrix_shared.usage_ledger; None when the ledger is unreadable)
    llm_cost_today_usd: float | None = None
    llm_calls_today: int = 0
    # dev_agent
    dev_failed_since_prev: list[tuple[int, str]] = field(default_factory=list)
    # (task_id, first description line, review_notes) newly awaiting an operator
    dev_awaiting_since_prev: list[tuple[int, str, str]] = field(default_factory=list)
    dev_stuck_running: list[int] = field(default_factory=list)


@dataclass(slots=True)
class HealthFlags:
    """Which conditions were active at the last tick and when we last alerted."""

    active: dict[str, datetime] = field(default_factory=dict)  # condition → last alert ts
    last_dev_failed_check: datetime | None = None


def _age(now: datetime, ts: datetime | None) -> float | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return max(0.0, (now - ts).total_seconds())  # exchange clocks can run ahead


def llm_configured() -> bool:
    """Same notion as subscription_llm.subscription_enabled(), env-only so the
    notify image doesn't need the Cursor CLI installed."""
    backend = os.environ.get("MATRIX_LLM_BACKEND", "").strip().lower()
    if backend == "cursor":
        return True
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip():
        return True
    if os.path.exists(os.path.join(os.path.expanduser("~/.claude"), ".credentials.json")):
        return True
    if os.environ.get("CLAUDE_CODE_USE_BEDROCK", "").strip() in ("1", "true"):
        return True
    if os.environ.get("CLAUDE_CODE_USE_VERTEX", "").strip() in ("1", "true"):
        return True
    if os.environ.get("OPENROUTER_API_KEY", "").strip():
        return True
    return False


async def collect_health(prev: HealthFlags, now: datetime | None = None) -> HealthSample:
    now = now or datetime.now(timezone.utc)
    s = HealthSample(now=now, llm_configured=llm_configured())

    # --- shared tier -----------------------------------------------------
    try:
        async with shared_session_scope() as session:
            ts = (await session.execute(text(
                "SELECT max(snapshot_ts) FROM wallet_snapshots"
            ))).scalar()
            s.paper_snapshot_age_s = _age(now, ts)

            ts = (await session.execute(text(
                "SELECT max(opened_at) FROM paper_positions"
            ))).scalar()
            s.paper_fill_age_s = _age(now, ts)

            ts = (await session.execute(text(
                "SELECT max(created_at) FROM predictions WHERE asset_class = 'crypto'"
            ))).scalar()
            s.crypto_prediction_age_s = _age(now, ts)

            row = (await session.execute(text(
                "SELECT count(*) AS n, "
                "       count(*) FILTER (WHERE context->>'method' LIKE 'llm%') AS n_llm "
                "FROM predictions "
                "WHERE strategy_id = 'matrix_agent' "
                "  AND context->>'method' IS NOT NULL "
                "  AND created_at >= now() - (:m || ' minutes')::interval"
            ), {"m": str(LLM_WINDOW_MIN)})).one()
            s.agent_predictions_in_window = int(row.n or 0)
            s.agent_llm_predictions_in_window = int(row.n_llm or 0)

    except Exception as e:  # noqa: BLE001 — health must never crash the poller
        logger.warning(f"health: shared-tier probe failed: {e}")

    # --- dev_agent (LOCAL tier: dev_agent writes to LOCAL_DATABASE_URL; the
    # shared copy of dev_tasks is an empty migration artefact, so probing it
    # there meant these alerts never fired) -----------------------------------
    try:
        async with local_session_scope() as session:
            since = prev.last_dev_failed_check or (now.replace(microsecond=0))
            rows = (await session.execute(text(
                "SELECT id, COALESCE(failure_reason, '') FROM dev_tasks "
                "WHERE status = 'failed' AND finished_at > :since ORDER BY id"
            ), {"since": since})).all()
            s.dev_failed_since_prev = [(int(r[0]), str(r[1])) for r in rows]

            rows = (await session.execute(text(
                "SELECT id, description, COALESCE(review_notes, '') FROM dev_tasks "
                "WHERE status = 'awaiting_review' AND finished_at > :since ORDER BY id"
            ), {"since": since})).all()
            s.dev_awaiting_since_prev = [
                (int(r[0]), str(r[1] or "").strip().splitlines()[0][:80] if r[1] else "", str(r[2])[:120])
                for r in rows
            ]

            rows = (await session.execute(text(
                "SELECT id FROM dev_tasks WHERE status = 'running' "
                "  AND heartbeat_at IS NOT NULL "
                "  AND heartbeat_at < now() - (:s || ' seconds')::interval"
            ), {"s": str(DEV_TASK_HEARTBEAT_STALE_S)})).all()
            s.dev_stuck_running = [int(r[0]) for r in rows]
    except Exception as e:  # noqa: BLE001
        logger.warning(f"health: dev_agent probe failed: {e}")

    # --- local tier (market data) -----------------------------------------
    try:
        async with local_session_scope() as session:
            # Raw stream liveness: newest ticker snapshot for the most liquid
            # symbol (index (symbol, snapshot_ts) makes this O(log n)). Pinned
            # to bybit: the Binance funding poller writes the same symbol, so
            # an unpinned probe stays green while the Bybit stream is dead.
            ts = (await session.execute(text(
                "SELECT snapshot_ts FROM market_ticker_snapshots "
                "WHERE symbol = :sym AND exchange = 'bybit' ORDER BY snapshot_ts DESC LIMIT 1"
            ), {"sym": os.environ.get("MATRIX_HEALTH_PROBE_SYMBOL", "BTCUSDT")})).scalar()
            s.crypto_tick_age_s = _age(now, ts)
            ts = (await session.execute(text(
                "SELECT ts FROM market_bars WHERE asset_class = 'crypto' "
                "  AND interval = '1m' ORDER BY ts DESC LIMIT 1"
            ))).scalar()
            s.crypto_bar_age_s = _age(now, ts)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"health: local-tier probe failed: {e}")

    # --- LLM spend today (shared usage ledger) --------------------------------
    try:
        from matrix_shared.usage_ledger import summary as _usage_summary
        u = _usage_summary(days=1)
        if u["calls"]:
            s.llm_cost_today_usd = float(u["cost_usd"])
            s.llm_calls_today = int(u["calls"])
    except Exception as e:  # noqa: BLE001
        logger.debug(f"health: usage ledger unavailable ({e})")

    # --- disk ----------------------------------------------------------------
    try:
        usage = shutil.disk_usage(os.environ.get("MATRIX_HEALTH_DISK_PATH", "/"))
        s.disk_free_pct = usage.free / usage.total * 100.0 if usage.total else None
    except OSError as e:
        logger.warning(f"health: disk probe failed: {e}")

    return s


def _fmt_age(age: float | None) -> str:
    if age is None:
        return "unknown"
    if age < 120:
        return f"{age:.0f}s"
    if age < 7200:
        return f"{age / 60:.0f}m"
    if age < 172800:
        return f"{age / 3600:.0f}h"
    return f"{age / 86400:.1f}d"


def detect_health_alerts(
    prev: HealthFlags, s: HealthSample, *, realert_s: int = REALERT_S
) -> tuple[list[tuple[str, str]], HealthFlags]:
    """Pure: compare sample to previous flags, return (alerts, new_flags)."""
    now = s.now
    conditions: dict[str, tuple[str, str]] = {}  # key → (level, text)

    if s.paper_snapshot_age_s is not None and s.paper_snapshot_age_s > PAPER_ENGINE_STALE_S:
        conditions["paper_engine_stalled"] = (
            ALERT_URGENT,
            f"⚠️ Paper engine stalled: last wallet snapshot {_fmt_age(s.paper_snapshot_age_s)} ago "
            f"(> {PAPER_ENGINE_STALE_S}s). Positions are not being marked/closed.",
        )
    if s.paper_fill_age_s is not None and s.paper_fill_age_s > FILL_STALE_S:
        conditions["no_fills"] = (
            ALERT_WARNING,
            f"⚡ No paper position opened for {_fmt_age(s.paper_fill_age_s)} "
            f"(> {FILL_STALE_S // 3600}h). The loops run but nothing trades — check slot "
            "allocations, the EV floor and backpressure.",
        )
    if s.crypto_prediction_age_s is not None and s.crypto_prediction_age_s > SIGNAL_STALE_S:
        conditions["signals_stalled"] = (
            ALERT_WARNING,
            f"⚡ No crypto predictions for {_fmt_age(s.crypto_prediction_age_s)} "
            f"(> {SIGNAL_STALE_S}s). agent/strategy loops may be wedged.",
        )
    if s.crypto_tick_age_s is not None and s.crypto_tick_age_s > INGEST_STALE_S:
        conditions["ingestion_stalled"] = (
            ALERT_URGENT,
            f"⚠️ Crypto ingestion stale: newest ticker snapshot {_fmt_age(s.crypto_tick_age_s)} old "
            f"(> {INGEST_STALE_S}s). Bybit WS stream down?",
        )
    if s.crypto_bar_age_s is not None and s.crypto_bar_age_s > BARS_STALE_S:
        conditions["bars_stalled"] = (
            ALERT_WARNING,
            f"⚡ Crypto 1m bars stale: newest bar {_fmt_age(s.crypto_bar_age_s)} old "
            f"(> {BARS_STALE_S}s). bars-aggregator wedged? Bar-based strategies are blind.",
        )
    if s.disk_free_pct is not None and s.disk_free_pct < DISK_FREE_MIN_PCT:
        conditions["disk_pressure"] = (
            ALERT_URGENT,
            f"⚠️ Disk free {s.disk_free_pct:.1f}% (< {DISK_FREE_MIN_PCT:.0f}%). "
            "Postgres will panic on a full disk — run `make disk-clean`, check retention.",
        )
    if (
        s.llm_configured
        and s.agent_predictions_in_window >= 5
        and s.agent_llm_predictions_in_window == 0
    ):
        conditions["llm_degraded"] = (
            ALERT_WARNING,
            f"⚡ LLM path inactive: {s.agent_predictions_in_window} matrix_agent predictions "
            f"in the last {LLM_WINDOW_MIN}m, none via LLM. Backend auth expired or breaker "
            "open — decisions are rule-only.",
        )
    if s.llm_cost_today_usd is not None and s.llm_cost_today_usd > LLM_DAILY_BUDGET_USD:
        conditions["llm_budget"] = (
            ALERT_WARNING,
            f"⚡ LLM spend today ${s.llm_cost_today_usd:.2f} over {s.llm_calls_today} calls exceeds the "
            f"${LLM_DAILY_BUDGET_USD:.0f}/day budget (MATRIX_LLM_DAILY_BUDGET_USD). Subscription is flat-rate, "
            "but this much volume eats the rate budget of the 15s decision loop.",
        )
    if s.dev_stuck_running:
        ids = ", ".join(str(i) for i in s.dev_stuck_running[:5])
        conditions["dev_task_stuck"] = (
            ALERT_WARNING,
            f"⚡ dev_agent task(s) running with stale heartbeat "
            f"(> {DEV_TASK_HEARTBEAT_STALE_S}s): #{ids}.",
        )

    alerts: list[tuple[str, str]] = []
    new_active: dict[str, datetime] = {}
    for key, (level, msg) in conditions.items():
        last = prev.active.get(key)
        if last is None or (now - last).total_seconds() >= realert_s:
            alerts.append((level, msg))
            new_active[key] = now
        else:
            new_active[key] = last
    for key in prev.active:
        if key not in conditions:
            alerts.append((ALERT_INFO, f"ℹ️ Recovered: {key.replace('_', ' ')}."))

    # Point events (not stateful): failed dev tasks since last check.
    for task_id, reason in s.dev_failed_since_prev:
        alerts.append((
            ALERT_WARNING,
            f"⚡ dev_agent task #{task_id} failed: {reason or 'no reason recorded'}.",
        ))

    for task_id, title, notes in s.dev_awaiting_since_prev:
        alerts.append((
            ALERT_INFO,
            f"🧩 dev_agent task #{task_id} awaiting review: {title or 'no description'}"
            + (f" — {notes}" if notes else "")
            + f"\nReply /dev_accept {task_id} · /dev_discard {task_id} · /dev_revise {task_id} <notes>",
        ))

    return alerts, HealthFlags(active=new_active, last_dev_failed_check=now)

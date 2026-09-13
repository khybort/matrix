"""LLM usage ledger without a migration (docs/AUTONOMY_PLAN.md P3.3 `agent_usage`).

Every LLM completion already emits one `agent.usage` log line; logs are not
queryable across containers. This appends the same record as JSON to a daily
file on the `matrix_claude_config` volume (mounted at ~/.claude in every LLM
service), so the Director digest and notify can sum spend/turns per service
per day. The alembic chain is blocked by uncommitted WIP; when it opens this
becomes a table and the reader below is the only thing that changes.

File: <CLAUDE_CONFIG_DIR|~/.claude>/matrix_usage/YYYY-MM-DD.jsonl — one JSON
object per line: ts, service, session, backend, model, turns, cost_usd,
is_error, reason. Best-effort: never raises into the caller.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from loguru import logger


def ledger_dir() -> Path:
    base = os.environ.get("MATRIX_USAGE_DIR") or os.path.join(
        os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"), "matrix_usage")
    return Path(base)


def service_name() -> str:
    """Compose sets the workdir to /workspace/services/<svc>; fall back to env."""
    env = os.environ.get("MATRIX_SERVICE")
    if env:
        return env
    cwd = Path(os.getcwd())
    return cwd.name if cwd.parent.name == "services" else "unknown"


def record(*, session: str | None, backend: str | None, model: str | None, turns: int | None,
           cost_usd: float | None, is_error: bool | None, reason: str | None = None,
           duration_s: float | None = None) -> None:
    now = datetime.now(UTC)
    row: dict[str, Any] = {
        "ts": now.isoformat(timespec="seconds"), "service": service_name(), "session": session,
        "backend": backend, "model": model, "turns": turns, "cost_usd": cost_usd,
        "is_error": is_error, "reason": reason,
        "duration_s": round(duration_s, 2) if isinstance(duration_s, (int, float)) else None,
    }
    try:
        d = ledger_dir()
        d.mkdir(parents=True, exist_ok=True)
        with (d / f"{now:%Y-%m-%d}.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
    except Exception as e:  # noqa: BLE001 — accounting must never break a call
        logger.debug(f"usage ledger: append failed ({e})")


def _iter_rows(day: datetime) -> list[dict[str, Any]]:
    path = ledger_dir() / f"{day:%Y-%m-%d}.jsonl"
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError as e:
        logger.debug(f"usage ledger: read failed ({e})")
    return out


def summary(day: datetime | None = None, *, days: int = 1) -> dict[str, Any]:
    """Per-service calls / turns / cost / errors over `days` ending at `day` (UTC)."""
    end = day or datetime.now(UTC)
    by: dict[str, dict[str, float]] = {}
    durations: dict[str, list[float]] = {}
    total_cost = 0.0
    calls = 0
    for i in range(days):
        for r in _iter_rows(end - timedelta(days=i)):
            svc = str(r.get("service") or "unknown")
            b = by.setdefault(svc, {"calls": 0, "turns": 0, "cost_usd": 0.0, "errors": 0, "timeouts": 0})
            b["calls"] += 1
            calls += 1
            if r.get("reason") == "timeout":
                b["timeouts"] += 1
            d = r.get("duration_s")
            if isinstance(d, (int, float)):
                durations.setdefault(svc, []).append(float(d))
            try:
                b["turns"] += int(r.get("turns") or 0)
            except (TypeError, ValueError):
                pass
            try:
                c = float(r.get("cost_usd") or 0.0)
            except (TypeError, ValueError):
                c = 0.0
            b["cost_usd"] += c
            total_cost += c
            if r.get("is_error"):
                b["errors"] += 1
    for svc, b in by.items():
        b["cost_usd"] = round(b["cost_usd"], 4)
        ds = sorted(durations.get(svc, []))
        if ds:
            b["p50_s"] = round(ds[len(ds) // 2], 1)
            b["p95_s"] = round(ds[min(len(ds) - 1, int(len(ds) * 0.95))], 1)
    return {"days": days, "calls": calls, "cost_usd": round(total_cost, 4),
            "by_service": dict(sorted(by.items(), key=lambda kv: -kv[1]["cost_usd"]))}


def last_record_ts(*, service: str | None = None, session: str | None = None) -> datetime | None:
    """Timestamp of the newest ledger row matching service/session (today or
    yesterday), or None. Lets a restarted daemon see when it last ran."""
    now = datetime.now(UTC)
    best: datetime | None = None
    for i in range(2):
        for r in _iter_rows(now - timedelta(days=i)):
            if service is not None and r.get("service") != service:
                continue
            if session is not None and r.get("session") != session:
                continue
            try:
                ts = datetime.fromisoformat(str(r.get("ts")))
            except (TypeError, ValueError):
                continue
            if best is None or ts > best:
                best = ts
    return best

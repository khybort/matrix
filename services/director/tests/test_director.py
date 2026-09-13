"""Director: digest rendering (pure), tool belt shape, dev-task dedupe (DB)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from matrix_shared import local_session_scope, shared_session_scope

from director import tools as T
from director.digest import SystemDigest, render_brief

pytestmark = pytest.mark.asyncio


def test_render_brief_is_compact_and_mentions_key_sections():
    d = SystemDigest(
        now=datetime(2026, 9, 13, 12, 0, tzinfo=UTC),
        health={"paper_snapshot_age_s": 3.0, "crypto_prediction_age_s": 12.0, "crypto_tick_age_s": 1.0,
                "crypto_bar_age_s": 90.0, "agent_predictions_60m": 30, "agent_llm_predictions_60m": 0,
                "disk_free_pct": 21.0, "local_db_gb": 97.0},
        wallets=[{"name": "default", "asset_class": "crypto", "equity": 9633.6, "net_pnl": -366.4,
                  "circuit_tripped": False}],
        pnl=[{"strategy_id": "grid", "asset_class": "crypto", "version": 4, "n_24h": 3, "pnl_24h": -0.5,
              "n_7d": 40, "pnl_7d": -8.1, "win_rate_7d": 0.41}],
        challengers=[{"strategy_id": "grid", "asset_class": "crypto", "version": 5, "n_outcomes": 12}],
        efficacy={"verdicts_7d": {"pending": 2}, "rollbacks_7d": 1, "cutovers_7d": 0, "challengers_retired_7d": 0},
        dev={"by_status_7d": {"failed": 8}, "pending": 0, "spend_today_usd": 0.0, "recent_failures": []},
        lessons={"crypto/avoid": 7},
        certs=[{"granted_by": "auto-eligibility+relaxed"}, {"granted_by": "auto-eligibility"}],
    )
    brief = render_brief(d)
    assert brief.startswith("🧭 Director brief 2026-09-13 12:00 UTC")
    assert "grid/crypto v4: -8.1 USD" in brief
    assert "challengers: grid/crypto v5 (n=12)" in brief
    assert "certs granted: 2 (1 relaxed/testnet-only)" in brief
    assert len(brief.splitlines()) <= 14


def test_tool_belt_side_effects_are_declared():
    state = T.TickState(SystemDigest(now=datetime.now(UTC)))
    reg = T.build_registry(state)
    effects = {t.name: t.side_effect for t in reg.all()}
    assert effects == {
        "system_digest": "read", "strategy_pnl": "read", "efficacy_report": "read",
        "dev_tasks_report": "read", "active_lessons": "read",
        "file_dev_task": "write", "retire_strategy": "write",
        "revoke_certificate": "risk-gated",
    }
    # Nothing in the belt can grant, enable or size anything.
    for name in effects:
        assert "grant" not in name and "enable" not in name and "wallet" not in name


async def test_file_dev_task_dedupes_and_respects_budget(monkeypatch):
    monkeypatch.setattr(T, "MAX_DEV_TASKS_PER_TICK", 1)
    state = T.TickState(SystemDigest(now=datetime.now(UTC)))
    reg = T.build_registry(state)
    tool = reg.get("file_dev_task")
    desc = f"Director test task {uuid.uuid4().hex}: read services/strategy/src/strategy/main.py, fix nothing, verify nothing."
    try:
        out = await tool.handler({"description": desc, "touches_files": ["services/strategy/"], "priority": 1})
        assert '"filed":true' in out["content"][0]["text"]
        assert len(state.dev_tasks_filed) == 1
        # budget exhausted → error, no second row
        out2 = await tool.handler({"description": desc + " again", "touches_files": [], "priority": 1})
        assert out2.get("is_error") is True
        # dedupe on a fresh tick state
        state2 = T.TickState(SystemDigest(now=datetime.now(UTC)))
        out3 = await T.build_registry(state2).get("file_dev_task").handler(
            {"description": desc, "touches_files": [], "priority": 1})
        assert '"filed":false' in out3["content"][0]["text"]
        async with local_session_scope() as s:  # dev_tasks is a LOCAL-tier table
            n = (await s.execute(text("SELECT count(*) FROM dev_tasks WHERE description LIKE :m"),
                                 {"m": f"%{uuid.UUID(desc.split()[3].rstrip(':')).hex}%"})).scalar()
            assert n == 1
    finally:
        async with local_session_scope() as s:
            await s.execute(text("DELETE FROM dev_tasks WHERE description LIKE :m"),
                            {"m": f"%{desc[:60]}%"})


async def test_retire_and_revoke_only_move_in_safe_direction():
    sid = f"dir_{uuid.uuid4().hex[:6]}"
    state = T.TickState(SystemDigest(now=datetime.now(UTC)))
    reg = T.build_registry(state)
    try:
        async with shared_session_scope() as s:
            await s.execute(text(
                "INSERT INTO strategy_configs (id, strategy_id, asset_class, version, status, params) "
                "VALUES (gen_random_uuid(), :sid, 'crypto', 1, 'active', '{}'::json)"), {"sid": sid})
            await s.execute(text(
                "INSERT INTO paper_trade_certificate (id, strategy_id, asset_class, version, status, granted_by) "
                "VALUES (gen_random_uuid(), :sid, 'crypto', 1, 'granted', 'test')"), {"sid": sid})
        out = await reg.get("retire_strategy").handler(
            {"strategy_id": sid, "asset_class": "crypto", "reason": "persistent loser in test fixture"})
        assert '"retired":true' in out["content"][0]["text"]
        out = await reg.get("revoke_certificate").handler(
            {"strategy_id": sid, "asset_class": "crypto", "version": 1, "reason": "losing money in test fixture"})
        assert '"revoked":true' in out["content"][0]["text"]
        async with shared_session_scope() as s:
            st = (await s.execute(text("SELECT status FROM strategy_configs WHERE strategy_id=:sid"), {"sid": sid})).scalar()
            cs = (await s.execute(text("SELECT status FROM paper_trade_certificate WHERE strategy_id=:sid"), {"sid": sid})).scalar()
            assert st == "retired" and cs == "revoked"
        # short reasons are refused
        out = await reg.get("retire_strategy").handler({"strategy_id": sid, "asset_class": "crypto", "reason": "meh"})
        assert out.get("is_error") is True
        assert [a["action"] for a in state.actions] == ["retire_strategy", "revoke_certificate"]
    finally:
        async with shared_session_scope() as s:
            await s.execute(text("DELETE FROM paper_trade_certificate WHERE strategy_id=:sid"), {"sid": sid})
            await s.execute(text("DELETE FROM strategy_configs WHERE strategy_id=:sid"), {"sid": sid})

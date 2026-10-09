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
              "n_7d": 40, "n_raw_7d": 95, "pnl_7d": -8.1, "win_rate_7d": 0.41}],
        challengers=[{"strategy_id": "grid", "asset_class": "crypto", "version": 5, "n": 12, "n_raw": 30}],
        efficacy={"verdicts_7d": {"pending": 2}, "rollbacks_7d": 1, "cutovers_7d": 0, "challengers_retired_7d": 0},
        dev={"by_status_7d": {"failed": 8}, "pending": 0, "spend_today_usd": 0.0, "recent_failures": []},
        lessons={"crypto/avoid": 7},
        certs=[{"granted_by": "auto-eligibility+relaxed"}, {"granted_by": "auto-eligibility"}],
    )
    brief = render_brief(d)
    assert brief.startswith("🧭 Director brief 2026-09-13 12:00 UTC")
    assert "grid/crypto v4: -8.1 USD" in brief
    assert "n=40 ep/95 fills" in brief
    assert "challengers: grid/crypto v5 (n=12 ep/30 fills)" in brief
    assert "certs granted: 2 (1 relaxed/testnet-only)" in brief
    assert len(brief.splitlines()) <= 14


def _fill(sec, pnl, *, sym="BTCUSDT", side="long", reason="hit_tp", version=1, method="rule"):
    from datetime import timedelta
    at = datetime(2026, 10, 1, 12, 0, tzinfo=UTC) + timedelta(seconds=sec)
    return {"strategy_id": "grid", "asset_class": "crypto", "version": version, "symbol": sym, "side": side,
            "generated_at": at, "horizon_seconds": 600, "method": method, "pnl_usd": pnl,
            "observed_at": at, "reason": reason}


def test_episode_summary_counts_a_refilled_bet_once():
    from director.digest import episode_summary

    # One bet filled three times (re-emitted inside its horizon), one separate
    # loser, one flat-close that is not evidence.
    rows = [_fill(0, 1.0), _fill(60, 1.0), _fill(120, -0.5), _fill(900, -1.0, sym="ETHUSDT"),
            _fill(30, 0.0, reason="orphan_flat_close")]
    (r,) = episode_summary(rows, ("strategy_id", "asset_class", "version"))
    assert (r["n"], r["n_raw"], r["n_unscorable"]) == (2, 4, 1)
    assert r["pnl"] == 0.5
    assert r["win_rate"] == 0.5  # per row it would have read 0.5 of 4 = 2 wins; per episode 1 of 2
    assert r["avg_pnl"] == 0.25


def test_ranker_summary_uses_episodes():
    from datetime import timedelta

    from director.digest import ranker_summary

    t0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    def p(sec, pct=None, virt=None, sym="BTCUSDT"):
        return {"strategy_id": "grid", "asset_class": "crypto", "symbol": sym, "side": "long",
                "generated_at": t0 + timedelta(seconds=sec), "horizon_seconds": 600,
                "pnl_pct": pct, "virtual_pct": virt}
    rows = [p(0, virt=0.01), p(30, pct=0.002), p(60, virt=0.03),   # one episode, traded
            p(0, virt=-0.004, sym="ETHUSDT"), p(40, virt=0.05, sym="ETHUSDT")]  # one, untraded
    (r,) = ranker_summary(rows)
    assert (r["n_traded"], r["n_traded_raw"], r["traded_bps"]) == (1, 1, 20.0)
    assert (r["n_untraded"], r["n_untraded_raw"], r["untraded_bps"]) == (1, 4, -40.0)


def test_tool_belt_side_effects_are_declared():
    state = T.TickState(SystemDigest(now=datetime.now(UTC)))
    reg = T.build_registry(state)
    effects = {t.name: t.side_effect for t in reg.all()}
    assert effects == {
        "system_digest": "read", "strategy_pnl": "read", "efficacy_report": "read",
        "dev_tasks_report": "read", "active_lessons": "read", "quant_research": "read",
        "file_dev_task": "write", "retire_strategy": "write",
        "revoke_certificate": "risk-gated",
    }
    # The research tools are read-only by construction: they replay history in
    # memory and must never reach capital.
    assert effects["quant_research"] == "read"
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


def test_render_brief_has_one_shadow_line_per_tracked_strategy():
    from matrix_shared import shadow_tracker as st

    band = st.DEFAULT_BANDS[("neg_funding_carry", "crypto")]
    now = datetime(2026, 10, 9, 16, 0, tzinfo=UTC)
    rep = st.evaluate([], band, now=now, qualifying=2, strategy_id="neg_funding_carry", asset_class="crypto")
    brief = render_brief(SystemDigest(now=now, shadow=[rep]))
    lines = [ln for ln in brief.splitlines() if ln.startswith("shadow ")]
    assert lines == [
        "shadow neg_funding_carry/crypto: COLLECTING — 0/20 closed ep, 0 open · band +100…+300, "
        "floor +30 · executable only: 0 would-abort ep excluded, 0 executor refused · last open never · "
        "2 qualifying settlements/72h"
    ]
    assert "shadow" in SystemDigest(now=now, shadow=[rep]).as_dict()
    assert "shadow tracker: no strategy has a registered band" in render_brief(SystemDigest(now=now, shadow=[]))
    assert "shadow tracker: unavailable" in render_brief(SystemDigest(now=now))

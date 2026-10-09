"""matrix_shared.research: every protocol guard, the ledger's invariants and
the shared building blocks. A guard without a test is a guard that a later
refactor removes silently — and then the harness can be gamed."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from matrix_shared.research import (
    Cell,
    Episode,
    FrozenCellError,
    HoldoutError,
    Ledger,
    LedgerError,
    LookaheadError,
    NotCommitted,
    Spec,
    SpecMismatch,
    WindowError,
    bhy_qvalues,
    clustered_t,
    costs,
    fetch,
    funding,
    non_overlapping,
    register,
    t_sf,
)
from matrix_shared.research.episodes import priced
from matrix_shared.research.stats import week_key

LEDGER = "docs/research/ledger.jsonl"
PREREG = "research/r9/PREREG.md"


class FakeGit:
    """Commits are dicts {path: bytes}; history is linear."""

    def __init__(self) -> None:
        self.history: list[tuple[str, dict[str, bytes]]] = []

    def commit(self, root: Path, *paths: str) -> str:
        h = f"{len(self.history):040x}"
        self.history.append((h, {p: (root / p).read_bytes() for p in paths}))
        return h

    def adding_commit(self, path):
        return next((h for h, files in self.history if path in files), None)

    def files_in(self, commit):
        return list(dict(self.history)[commit])

    def blob_at(self, commit, path):
        out = None
        for h, files in self.history:
            out = files.get(path, out)
            if h == commit:
                return out
        return None

    def head_blob(self, path):
        return self.blob_at(self.history[-1][0], path) if self.history else None


def _spec(**kw) -> Spec:
    base = dict(
        round="r9",
        title="toy",
        question="does the toy signal pay?",
        cells=(Cell("a", {"x": 1}), Cell("b", {"x": 2})),
        train=("2026-01-01", "2026-03-01"),
        holdout=("2026-03-01", "2026-05-01"),
        cluster="day",
        min_entry_lag_s=60,
    )
    base.update(kw)
    return Spec(**base)


@pytest.fixture
def env(tmp_path):
    git = FakeGit()
    ledger = Ledger(tmp_path / LEDGER, git=git, repo_path=LEDGER)

    def reg(spec=None, prereg=PREREG, **kw):
        return register(spec or _spec(), prereg, ledger=ledger, git=git, root=tmp_path, **kw)

    def commit_ledger():
        git.commit(tmp_path, LEDGER)

    return tmp_path, git, ledger, reg, commit_ledger


def _registered(env, spec=None):
    root, git, _ledger, reg, _ = env
    with pytest.raises(NotCommitted):
        reg(spec)
    git.commit(root, PREREG)
    return reg(spec)


def _eps(start, end, mean, n=40, lag=timedelta(minutes=5), sym=None):
    """n non-overlapping episodes spread over [start, end), alternating around `mean`."""
    step = (end - start) / n
    return [
        Episode(
            sym or f"S{i % 4}",
            start + i * step,
            start + i * step + lag,
            start + i * step + lag + timedelta(minutes=1),
            mean + (10 if i % 2 else -10) + (i % 7),
        )
        for i in range(n)
    ]


def builder_good(cell, start, end):  # cell a pays, cell b loses
    return _eps(start, end, 50.0 if cell.id == "a" else -50.0)


# ---------------------------------------------------------------- registration
def test_register_writes_prereg_then_refuses_until_committed(env):
    root, _git, ledger, reg, _ = env
    with pytest.raises(NotCommitted, match="commit it alone"):
        reg()
    assert (root / PREREG).exists() and "harness-spec" in (root / PREREG).read_text()
    with pytest.raises(NotCommitted, match="not committed"):
        reg()
    assert ledger.m == 0


def test_register_refuses_prereg_committed_with_other_files(env):
    root, git, _ledger, reg, _ = env
    with pytest.raises(NotCommitted):
        reg()
    (root / "results.csv").write_text("x")
    git.commit(root, PREREG, "results.csv")
    with pytest.raises(NotCommitted, match="must be alone"):
        reg()


def test_register_refuses_spec_changed_after_commit(env):
    _root, _git, _ledger, reg, _ = env
    _registered(env)
    with pytest.raises(SpecMismatch):
        reg(_spec(min_t=1.5))


def test_register_refuses_edited_spec_block(env):
    root, git, _ledger, reg, _ = env
    with pytest.raises(NotCommitted):
        reg()
    git.commit(root, PREREG)
    p = root / PREREG
    p.write_text(p.read_text().replace('"min_t": 2.0', '"min_t": 1.0'))
    with pytest.raises(SpecMismatch, match="edited"):
        reg()


def test_register_records_every_cell(env):
    study = _registered(env)
    assert not study.replicate
    assert env[2].m == 2
    rows = env[2].rows()
    assert {r["test_id"] for r in rows} == {"r9.a", "r9.b"}
    assert all(r["prereg_commit"] == study.commit for r in rows)


def test_frozen_cell_with_changed_params_is_refused(env):
    root, git, _ledger, reg, _ = env
    _registered(env)
    changed = _spec(cells=(Cell("a", {"x": 99}), Cell("b", {"x": 2})))
    other = "research/r9/PREREG_v2.md"
    with pytest.raises(NotCommitted):
        reg(changed, prereg=other)
    git.commit(root, other)
    with pytest.raises(FrozenCellError, match="frozen"):
        reg(changed, prereg=other)


def test_same_spec_again_is_a_replicate_that_writes_nothing(env):
    _root, _git, ledger, reg, _commit_ledger = env
    study = _registered(env)
    study.evaluate(builder_good)
    n = len(ledger.lines())
    again = reg()
    assert again.replicate
    res = again.evaluate(builder_good)
    assert res["r9.a"].mean == study.results["train"]["r9.a"].mean
    assert len(ledger.lines()) == n
    with pytest.raises(HoldoutError, match="never opened"):
        again.open_holdout("a", builder_good)


def test_legacy_prereg_only_replicates(env):
    root, git, _ledger, reg, _ = env
    (root / "legacy.txt").write_text("free text pre-registration")
    git.commit(root, "legacy.txt")
    with pytest.raises(SpecMismatch, match="legacy"):
        reg(prereg="legacy.txt", legacy=True)


# ---------------------------------------------------------------- train / holdout
def test_train_verdict_is_recorded_and_frozen(env):
    study = _registered(env)
    res = study.evaluate(builder_good)
    assert res["r9.a"].passed and not res["r9.b"].passed
    assert env[2].tests()["r9.a"].train["passed"] is True
    with pytest.raises(FrozenCellError):
        study.evaluate(builder_good)


def test_no_holdout_before_train(env):
    study = _registered(env)
    with pytest.raises(HoldoutError, match="before the train verdict"):
        study.open_holdout("a", builder_good)


def test_evaluate_cannot_run_the_holdout_split(env):
    study = _registered(env)
    with pytest.raises(HoldoutError):
        study.evaluate(builder_good, split="holdout")


def test_holdout_requires_committed_train_verdict(env):
    study = _registered(env)
    study.evaluate(builder_good)
    with pytest.raises(HoldoutError, match="commit the ledger"):
        study.open_holdout("a", builder_good)


def test_holdout_only_for_train_passers_and_only_once(env):
    study = _registered(env)
    study.evaluate(builder_good)
    env[4]()
    with pytest.raises(HoldoutError, match="failed train"):
        study.open_holdout("b", builder_good)
    r = study.open_holdout("a", builder_good)
    assert r.passed
    with pytest.raises(HoldoutError, match="already opened"):
        study.open_holdout("a", builder_good)


def test_holdout_is_spent_before_it_is_computed(env):
    study = _registered(env)
    study.evaluate(builder_good)
    env[4]()

    def crashes(cell, start, end):
        raise RuntimeError("crash mid-holdout")

    with pytest.raises(RuntimeError):
        study.open_holdout("a", crashes)
    assert env[2].tests()["r9.a"].holdout_open is not None
    with pytest.raises(HoldoutError, match="already opened"):
        study.open_holdout("a", builder_good)


def test_record_holdout_is_for_failures_and_never_in_q(env):
    study = _registered(env)
    study.evaluate(builder_good)
    with pytest.raises(HoldoutError, match="decision"):
        study.record_holdout("a", builder_good)
    study.record_holdout("b", lambda c, s, e: _eps(s, e, 500.0))  # a huge record-only "win"
    assert env[2].tests()["r9.b"].family_p == 1.0
    with pytest.raises(HoldoutError, match="already written"):
        study.record_holdout("b", builder_good)


def test_lookahead_entry_at_information_time_is_refused(env):
    study = _registered(env)
    with pytest.raises(LookaheadError):
        study.evaluate(lambda c, s, e: _eps(s, e, 50.0, lag=timedelta(0)))


def test_lookahead_entry_inside_the_lag_is_refused(env):
    study = _registered(env)
    with pytest.raises(LookaheadError):
        study.evaluate(lambda c, s, e: _eps(s, e, 50.0, lag=timedelta(seconds=30)))  # spec lag 60 s
    assert env[2].tests()["r9.a"].train is None  # nothing recorded on a refused run


def test_builder_leaking_holdout_signals_into_train_is_refused(env):
    study = _registered(env)
    ho_start = datetime(2026, 3, 1, tzinfo=UTC)
    with pytest.raises(WindowError):
        study.evaluate(lambda c, s, e: _eps(s, ho_start + timedelta(days=10), 50.0))


def test_finalise_needs_the_passers_holdout_and_uses_ledger_q(env):
    study = _registered(env)
    study.evaluate(builder_good)
    with pytest.raises(Exception, match="open its holdout"):
        study.finalise()
    env[4]()
    study.open_holdout("a", builder_good)
    out = {r["test_id"]: r for r in study.finalise()}
    assert out["r9.a"]["verdict"] == "survives" and out["r9.a"]["m"] == 2
    assert out["r9.b"]["verdict"] == "rejected_train"
    assert out["r9.a"]["q"] == pytest.approx(env[2].qvalues()["r9.a"])


def test_q_grows_with_every_test_ever_registered(env):
    root, git, ledger, reg, _ = env
    study = _registered(env)
    study.evaluate(builder_good)
    env[4]()
    study.open_holdout("a", builder_good)
    q_small = ledger.qvalues()["r9.a"]
    many = _spec(round="r10", cells=tuple(Cell(f"c{i}", {"i": i}) for i in range(50)))
    p2 = "research/r10/PREREG.md"
    with pytest.raises(NotCommitted):
        reg(many, prereg=p2)
    git.commit(root, p2)
    reg(many, prereg=p2)
    assert ledger.m == 52
    assert ledger.qvalues()["r9.a"] > q_small * 20


# ---------------------------------------------------------------- ledger
def test_ledger_detects_an_edited_row(env):
    study = _registered(env)
    study.evaluate(builder_good)
    p = env[2].path
    lines = p.read_text().splitlines()
    row = json.loads(lines[2])  # cell a's train verdict, not the last row
    row["mean"] = 999.0
    lines[2] = json.dumps(row, sort_keys=True, separators=(",", ":"))
    p.write_text("\n".join(lines) + "\n")
    with pytest.raises(LedgerError, match="chain"):
        env[2].verify()


def test_ledger_refuses_to_append_over_rewritten_committed_history(env):
    _root, _git, ledger, _reg, commit_ledger = env
    _registered(env)
    commit_ledger()
    ledger.path.write_text("")  # wipe the committed rows
    with pytest.raises(LedgerError, match="committed version"):
        ledger.append({"kind": "note", "text": "x"})


def test_ledger_order_rules_hold_for_direct_appends(tmp_path):
    led = Ledger(tmp_path / "l.jsonl")
    with pytest.raises(LedgerError, match="not registered"):
        led.append({"kind": "train", "test_id": "x.a"})
    led.append({"kind": "register", "test_id": "x.a", "params": {}})
    with pytest.raises(LedgerError, match="already registered"):
        led.append({"kind": "register", "test_id": "x.a", "params": {}})
    with pytest.raises(LedgerError, match="before the train"):
        led.append({"kind": "holdout_open", "test_id": "x.a"})
    led.append({"kind": "train", "test_id": "x.a", "passed": False, "p": 0.9})
    with pytest.raises(LedgerError, match="failed train"):
        led.append({"kind": "holdout_open", "test_id": "x.a"})
    with pytest.raises(LedgerError, match="without holdout_open"):
        led.append({"kind": "holdout", "test_id": "x.a", "record_only": False, "p": 1e-9})
    led.verify()
    assert led.qvalues() == {"x.a": 1.0}


# ---------------------------------------------------------------- building blocks
def test_non_overlapping_keeps_first_and_unpriced_still_occupies():
    t = datetime(2026, 1, 1, tzinfo=UTC)
    h = timedelta(hours=1)
    eps = [
        Episode("A", t, t + h, t + 5 * h, math.nan),  # unpriced, occupies A until t+5h
        Episode("A", t + 2 * h, t + 3 * h, t + 4 * h, 10.0),  # inside -> dropped
        Episode("A", t + 5 * h, t + 6 * h, t + 7 * h, 20.0),  # at the exit -> kept
        Episode("B", t + 2 * h, t + 3 * h, t + 4 * h, 30.0),
    ]
    kept, excluded = priced(non_overlapping(eps))
    assert [e.net_bps for e in kept] == [30.0, 20.0] and excluded == 1


def test_t_sf_known_values():
    assert t_sf(2.0, 10) == pytest.approx(0.03669, abs=1e-5)
    assert t_sf(0.0, 5) == pytest.approx(0.5)
    assert t_sf(-1.812, 10) == pytest.approx(0.95, abs=1e-3)
    assert t_sf(8.139, 18) < 1e-6


def test_clustered_t_collapses_to_cluster_sums():
    vals = [1.0, 3.0, 2.0, 4.0, 6.0, 8.0]
    cl = ["a", "a", "b", "b", "c", "c"]
    c = clustered_t(vals, cl)
    mu = sum(vals) / 6
    sums = [(1 + 3) - 2 * mu, (2 + 4) - 2 * mu, (6 + 8) - 2 * mu]
    se = math.sqrt(sum(s * s for s in sums) * 3 / 2) / 6
    assert c.t == pytest.approx(mu / se) and c.clusters == 3
    assert c.p == pytest.approx(t_sf(mu / se, 2))
    assert math.isnan(clustered_t([1.0, 2.0, 3.0], ["a", "a", "a"]).t)


def test_bhy_matches_edge_study_decisions():
    from matrix_shared.edge_study import benjamini_yekutieli

    ps = [0.0001, 0.004, 0.019, 0.03, 0.2, 0.5, 0.9, 1.0]
    q = bhy_qvalues(ps)
    assert [x <= 0.05 for x in q] == benjamini_yekutieli(ps, 0.05)
    # padding with unlisted tests (p = 1) == passing m explicitly
    assert bhy_qvalues(ps[:3], m=8)[0] == pytest.approx(bhy_qvalues(ps[:3] + [1.0] * 5)[0])


def test_week_key_is_iso_monday_to_sunday():
    assert week_key(datetime(2026, 10, 4, 23, tzinfo=UTC)) != week_key(
        datetime(2026, 10, 5, 0, tzinfo=UTC)
    )
    assert week_key(datetime(2026, 10, 5, tzinfo=UTC)) == week_key(
        datetime(2026, 10, 11, 23, tzinfo=UTC)
    )


def test_funding_counts_each_settlement_once_in_half_open_window():
    t = datetime(2026, 1, 1, tzinfo=UTC)
    h = timedelta(hours=1)
    sett = [(t, 0.01), (t + 8 * h, 0.001), (t + 16 * h, -0.002), (t + 24 * h, 0.5)]
    # short perp from t (excluded) to t+16h (included): receives +rates
    bps, n = funding.funding_bps(sett, t, t + 16 * h, perp_side=-1, entry_price=100.0)
    assert n == 2 and bps == pytest.approx((0.001 - 0.002) * 1e4)
    # marked to market: price doubled at the first settlement
    px = {t + 8 * h: 200.0}
    bps, _ = funding.funding_bps(
        sett, t, t + 16 * h, perp_side=1, entry_price=100.0, price_at=px.get
    )
    assert bps == pytest.approx(-(0.001 * 2 - 0.002) * 1e4)
    assert funding.ceil_hour(t + timedelta(minutes=1)) == t + h and funding.ceil_hour(t) == t


def test_walk_matches_carry_books_semantics():
    bids = [(99.0, 2.0), (98.0, 10.0)]
    asks = [(101.0, 2.0), (102.0, 10.0)]
    assert costs.walk_bps(asks, 100.0, 101.0) == pytest.approx(100.0)  # one full level at 1 %
    assert costs.walk_bps(asks, 100.0, 202.0 + 102.0) == pytest.approx(
        (202 * 0.01 + 102 * 0.02) / 304 * 1e4
    )
    assert costs.walk_bps(asks, 100.0, 1e9) is None
    assert math.isnan(costs.hedged_cost_bps((bids, asks), ([], asks), 100.0, 31.0))
    assert costs.hedged_cost_bps((bids, asks), (bids, asks), 50.0, 31.0) == pytest.approx(
        31.0 + 4 * 100.0
    )
    assert costs.fees(("bybit", "perp"), ("bybit", "spot")) == 31.0


# ---------------------------------------------------------------- fetch
class FakeNet:
    def __init__(self, responses):
        self.responses = list(responses)
        self.urls: list[str] = []

    def __call__(self, url, timeout):
        self.urls.append(url)
        return self.responses.pop(0)


def _fetcher(tmp_path, net, **kw):
    clock = {"t": 0.0}
    sleeps: list[float] = []

    def sleep(s):
        sleeps.append(s)
        clock["t"] += s

    f = fetch.PoliteFetcher(
        tmp_path / "cache",
        opener=net,
        sleep=sleep,
        clock=lambda: clock["t"],
        wall=lambda: 30.0,
        **kw,
    )
    return f, sleeps


def test_fetch_418_bans_the_host_for_the_process(tmp_path):
    net = FakeNet([(418, {}, b"")])
    f, _ = _fetcher(tmp_path, net)
    with pytest.raises(fetch.BannedError):
        f.get_json("https://api.binance.com/api/v3/klines?x=1")
    with pytest.raises(fetch.BannedError):
        f.get_json("https://api.binance.com/api/v3/klines?x=2")
    assert len(net.urls) == 1  # the second call never left the process


def test_fetch_429_honours_retry_after_then_caches(tmp_path):
    net = FakeNet([(429, {"retry-after": "7"}, b""), (200, {}, b'{"ok": 1}')])
    f, sleeps = _fetcher(tmp_path, net)
    assert f.get_json("https://api.bybit.com/x") == {"ok": 1}
    assert 7.0 in sleeps
    assert f.get_json("https://api.bybit.com/x") == {"ok": 1}
    assert len(net.urls) == 2 and f.cache_hits == 1


def test_fetch_spaces_requests_per_host_and_reads_binance_weight(tmp_path):
    net = FakeNet([(200, {}, b"1"), (200, {"x-mbx-used-weight-1m": "950"}, b"2"), (200, {}, b"3")])
    f, sleeps = _fetcher(tmp_path, net)
    f.get_json("https://api.binance.com/a", cache=False)
    f.get_json("https://api.binance.com/b", cache=False)
    assert any(abs(s - 0.25) < 1e-9 for s in sleeps)  # min interval
    assert any(s >= 30 for s in sleeps)  # weight above the soft limit -> wait for the next minute


def test_bybit_klines_are_clipped_to_the_window(tmp_path):
    # a pair delisted before the window: Bybit still returns its newest bars before `end`
    old = [[str(1_000_000 - i * 3_600_000), "1", "2", "0.5", "1.5", "10", "15"] for i in range(5)]
    net = FakeNet([(200, {}, json.dumps({"retCode": 0, "result": {"list": old}}).encode())])
    f, _ = _fetcher(tmp_path, net)
    assert fetch.bybit_klines(f, "spot", "ZECUSDT", "60", 2_000_000_000, 3_000_000_000) == []
    assert fetch.clip([(5, 1), (1, 2), (5, 3), (9, 4)], 2, 8) == [(5, 3)]


def test_committed_ledger_reproduces_programme_m_and_h1(tmp_path):
    """The real ledger, when the repo is mounted (host runs; the image mounts only src/tests)."""
    root = Path(__file__).resolve().parents[3]
    p = root / LEDGER
    if not p.exists():
        pytest.skip("docs/ not mounted")
    led = Ledger(p)
    led.verify()
    assert led.m >= 91
    q = led.qvalues()
    assert q["r1.H1"] < 0.05
    assert sum(1 for x in q.values() if x <= 0.05) == 1

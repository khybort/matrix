"""The research protocol as code: register -> train -> holdout -> finalise.

    spec = Spec(round="r5", title=..., question=..., cells=(Cell("X08_h48", {...}), ...),
                train=("2025-10-01", "2026-06-01"), holdout=("2026-06-01", "2026-10-10"))
    study = register(spec, "services/backtest/research/signal_2026_11/PREREG.md")
        # 1st call writes the file and raises NotCommitted; commit it ALONE, call again
    train = study.evaluate(builder)                 # every cell, train window only
    for cid, r in train.items():
        if r.passed:
            study.open_holdout(cid, builder)        # once per cell, ledger-recorded first
    study.finalise()                                # verdicts + cumulative BHY q from the ledger

`builder(cell, start, end) -> iterable[Episode]` is the only code a study
writes; the harness owns everything that can be gamed.

Forward test (`Spec(forward_n=60, ...)`): a rule already seen in history is
re-tested on data that did not exist when it was registered. `train` names
the in-sample window it came from (cited, never evaluated); `holdout` is the
forward window. Nothing is evaluated until the first `forward_n` episodes by
signal time have all closed; `forward_status()` counts them (no returns), and
`open_forward()` then decides once, on exactly those `forward_n`, with the same
gate and the cumulative BHY q of the ledger at that moment.

Hard guards (each raises; tests in packages/python-shared/tests/test_research_harness.py):
- the pre-registration must be committed, by a commit that touches nothing
  else, and the spec block in that commit must equal the spec being run;
- a cell id is frozen: re-registering it with different params raises
  `FrozenCellError`; the same params re-run as a *replicate* that never writes
  to the ledger and can never open a holdout that was not opened before;
- no holdout before the cell's train verdict is in the ledger AND committed, none for a
  train failure (record-only rows exist for those, never counted in q), and
  at most one per cell — `holdout_open` is written before the episodes are
  built, so a crash still spends it;
- every episode enters strictly after its information time, by at least
  `min_entry_lag_s`, and its signal lies inside the split it was built for;
- one sample per episode (per symbol, no entry before the previous exit),
  enforced on what the builder returns.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from matrix_shared.research.episodes import (
    Episode,
    check_information_time,
    check_window,
    non_overlapping,
    priced,
)
from matrix_shared.research.ledger import Ledger, LedgerError
from matrix_shared.research.stats import CLUSTERS, clustered_t

UTC = timezone.utc  # noqa: UP017 — datetime.UTC is 3.11+, the host python (pandas) is 3.9

DEFAULT_LEDGER = "docs/research/ledger.jsonl"


class ProtocolError(RuntimeError):
    pass


class NotCommitted(ProtocolError):
    """The pre-registration is not (cleanly, alone) in git yet."""


class SpecMismatch(ProtocolError):
    """The spec being run differs from the one that was committed."""


class FrozenCellError(ProtocolError):
    """A cell id already in the ledger is being run with different parameters."""


class HoldoutError(ProtocolError):
    pass


# ---- git -------------------------------------------------------------------
class SubprocessGit:
    """The four questions the protocol asks git. Tests substitute a fake."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(self.root), *args], capture_output=True, check=False
        )

    def adding_commit(self, path: str) -> str | None:
        r = self._run("log", "--diff-filter=A", "--format=%H", "--", path)
        hashes = r.stdout.decode().split()
        return hashes[-1] if r.returncode == 0 and hashes else None

    def files_in(self, commit: str) -> list[str]:
        r = self._run("show", "--name-only", "--format=", commit)
        return [ln for ln in r.stdout.decode().splitlines() if ln.strip()]

    def blob_at(self, commit: str, path: str) -> bytes | None:
        r = self._run("show", f"{commit}:{path}")
        return r.stdout if r.returncode == 0 else None

    def head_blob(self, path: str) -> bytes | None:
        return self.blob_at("HEAD", path)


def repo_root(start: str | Path = ".") -> Path:
    r = subprocess.run(
        ["git", "-C", str(start), "rev-parse", "--show-toplevel"], capture_output=True, check=True
    )
    return Path(r.stdout.decode().strip())


# ---- spec ------------------------------------------------------------------
@dataclass(frozen=True)
class Cell:
    id: str
    params: Mapping = field(default_factory=dict)


@dataclass(frozen=True)
class Spec:
    round: str
    title: str
    question: str
    cells: tuple[Cell, ...]
    train: tuple[str, str]  # ISO dates, [start, end) by signal time
    holdout: tuple[str, str]
    family: str = ""  # hypothesis label shared by the cells, e.g. "H1-mirror"
    cluster: str = "week"
    min_t: float = 2.0
    q_max: float = 0.05
    min_entry_lag_s: float = 1.0
    data: str = ""
    costs: str = ""
    survivorship: str = ""
    notes: str = ""
    forward_n: int = 0  # > 0: forward test, decided once on the first forward_n episodes

    def __post_init__(self) -> None:
        if self.cluster not in CLUSTERS:
            raise ValueError(f"cluster must be one of {sorted(CLUSTERS)}")
        ids = [c.id for c in self.cells]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate cell ids")
        if not re.fullmatch(r"[A-Za-z0-9_.\-]+", self.round):
            raise ValueError("round id: letters, digits, _ . - only")
        if self.forward_n < 0:
            raise ValueError("forward_n must be >= 0")
        if self.min_entry_lag_s <= 0:
            raise ValueError(
                "min_entry_lag_s must be > 0: entry strictly after the information time"
            )
        tr, ho = _window(self.train), _window(self.holdout)
        if tr[1] > ho[0]:
            raise ValueError("train must end before the holdout starts")

    def to_dict(self) -> dict:
        d = {
            "round": self.round,
            "title": self.title,
            "question": self.question,
            "family": self.family,
            "cells": [{"id": c.id, "params": dict(c.params)} for c in self.cells],
            "train": list(self.train),
            "holdout": list(self.holdout),
            "cluster": self.cluster,
            "min_t": self.min_t,
            "q_max": self.q_max,
            "min_entry_lag_s": self.min_entry_lag_s,
            "data": self.data,
            "costs": self.costs,
            "survivorship": self.survivorship,
            "notes": self.notes,
        }
        if self.forward_n:  # absent for classic studies: their committed spec blocks stay valid
            d["forward_n"] = self.forward_n
        return d

    def canonical(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, indent=1, ensure_ascii=False)

    def hash(self) -> str:
        return hashlib.sha256(
            json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def test_id(self, cell_id: str) -> str:
        return f"{self.round}.{cell_id}"


def _window(w: tuple[str, str]) -> tuple[datetime, datetime]:
    a, b = (datetime.fromisoformat(x) for x in w)
    a = a if a.tzinfo else a.replace(tzinfo=UTC)
    b = b if b.tzinfo else b.replace(tzinfo=UTC)
    if not a < b:
        raise ValueError(f"empty window {w}")
    return a, b


SPEC_FENCE = re.compile(r"```json harness-spec\n(.*?)\n```", re.S)


def render_prereg(spec: Spec) -> str:
    cells = "\n".join(
        f"- `{spec.test_id(c.id)}`: {json.dumps(dict(c.params), sort_keys=True)}"
        for c in spec.cells
    )
    if spec.forward_n:
        split = f"""## Split
Forward test. No train split is evaluated: the rule's in-sample evidence is the window
{spec.train[0]} .. {spec.train[1]} (cited, not re-tested). The forward window is {spec.holdout[0]} ..
{spec.holdout[1]} (exclusive), by signal time. It is evaluated ONCE, when the first {spec.forward_n}
episodes by signal time have all closed; those {spec.forward_n} are the sample, later ones are ignored.
Before that only their count is read (`forward_status`), never a return."""
        decision = f"""## Decision
Success: mean net > 0 and {spec.cluster}-clustered t >= {spec.min_t} over the {spec.forward_n} episodes, and
cumulative Benjamini-Yekutieli q <= {spec.q_max} over every test in docs/research/ledger.jsonl at evaluation
time. Entry at least {spec.min_entry_lag_s:g} s after the information time. One sample per episode."""
    else:
        split = f"""## Split
Train {spec.train[0]} .. {spec.train[1]} (exclusive), holdout {spec.holdout[0]} .. {spec.holdout[1]}
(exclusive), by signal time. Holdout opened once, only for train passers, rule frozen."""
        decision = f"""## Decision
Train pass: mean net > 0 and {spec.cluster}-clustered t >= {spec.min_t}. Holdout: the same gate, and
cumulative Benjamini-Yekutieli q <= {spec.q_max} over every test in docs/research/ledger.jsonl.
Entry at least {spec.min_entry_lag_s:g} s after the information time. One sample per episode."""
    return f"""# Pre-registration — {spec.round}: {spec.title}
Written {datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")}, before any return was computed.
Generated by matrix_shared.research; the spec block below is what the harness runs.
Commit this file ALONE before evaluating anything; changing the block afterwards is refused.

## Question
{spec.question}

## Cells ({len(spec.cells)})
{cells}

{split}

{decision}

## Data / costs / survivorship
{spec.data}

{spec.costs}

{spec.survivorship}

{spec.notes}

## Spec
```json harness-spec
{spec.canonical()}
```

## Log
"""


def _spec_block(text: str) -> dict | None:
    m = SPEC_FENCE.search(text)
    return json.loads(m.group(1)) if m else None


# ---- results ---------------------------------------------------------------
@dataclass
class CellResult:
    test_id: str
    split: str
    n: int
    excluded: int
    mean: float
    median: float
    t: float
    clusters: int
    p: float
    passed: bool
    t_other: float  # t under the other clustering (day <-> week), descriptive
    parts: dict = field(default_factory=dict)
    episodes: list[Episode] = field(default_factory=list, repr=False)

    def row(self) -> dict:
        def r(x, nd=3):
            return (
                round(x, nd)
                if isinstance(x, float) and math.isfinite(x)
                else (None if isinstance(x, float) else x)
            )

        return {
            "split": self.split,
            "n": self.n,
            "excluded": self.excluded,
            "mean": r(self.mean, 2),
            "median": r(self.median, 2),
            "t": r(self.t),
            "clusters": self.clusters,
            "p": self.p if math.isfinite(self.p) else None,
            "passed": self.passed,
            "t_other": r(self.t_other),
            "parts": {k: r(v, 2) for k, v in self.parts.items()},
        }


Builder = Callable[[Cell, datetime, datetime], Iterable[Episode]]


# ---- the study -------------------------------------------------------------
class Study:
    def __init__(
        self, spec: Spec, prereg: str, commit: str, ledger: Ledger, *, replicate: bool
    ) -> None:
        self.spec, self.prereg, self.commit, self.ledger = spec, prereg, commit, ledger
        self.replicate = replicate
        self.results: dict[str, dict[str, CellResult]] = {"train": {}, "holdout": {}, "record": {}}

    def _cell(self, cell_id: str) -> Cell:
        for c in self.spec.cells:
            if c.id == cell_id or self.spec.test_id(c.id) == cell_id:
                return c
        raise KeyError(cell_id)

    def _build(self, cell: Cell, split: str, builder: Builder, end: datetime | None = None) -> list[Episode]:
        start, stop = _window(self.spec.train if split == "train" else self.spec.holdout)
        stop = min(stop, end) if end is not None else stop
        eps = list(builder(cell, start, stop))
        check_window(eps, start, stop)
        check_information_time(eps, timedelta(seconds=self.spec.min_entry_lag_s))
        return non_overlapping(eps)

    def _run(self, cell: Cell, split: str, builder: Builder) -> CellResult:
        return self._result(cell, split, *priced(self._build(cell, split, builder)))

    def _result(self, cell: Cell, split: str, kept: list[Episode], excluded: int) -> CellResult:
        key = CLUSTERS[self.spec.cluster]
        other = CLUSTERS["day" if self.spec.cluster == "week" else "week"]
        nets = [e.net_bps for e in kept]
        c = clustered_t(nets, [key(e.info_time) for e in kept])
        o = clustered_t(nets, [other(e.info_time) for e in kept])
        passed = bool(math.isfinite(c.t) and c.mean > 0 and c.t >= self.spec.min_t)
        parts: dict[str, float] = {}
        for e in kept:
            for k, v in e.parts.items():
                if isinstance(v, (int, float)) and math.isfinite(v):
                    parts[k] = parts.get(k, 0.0) + v
        parts = {k: v / len(kept) for k, v in parts.items()} if kept else {}
        return CellResult(
            self.spec.test_id(cell.id),
            split,
            c.n,
            excluded,
            c.mean,
            c.median,
            c.t,
            c.clusters,
            c.p,
            passed,
            o.t,
            parts,
            kept,
        )

    def evaluate(self, builder: Builder, split: str = "train") -> dict[str, CellResult]:
        """Train verdict for every cell, recorded in the ledger as it is reached."""
        if self.spec.forward_n:
            raise HoldoutError(
                "forward test: no train split; open_forward() decides once forward_n episodes closed"
            )
        if split != "train":
            raise HoldoutError(
                "the holdout is opened per cell with open_holdout(), after the train verdict"
            )
        tests = self.ledger.tests()
        if not self.replicate:
            done = [
                c.id for c in self.spec.cells if tests[self.spec.test_id(c.id)].train is not None
            ]
            if done:
                raise FrozenCellError(f"train verdict already recorded for {done}; it is frozen")
        out = {}
        for cell in self.spec.cells:
            res = self._run(cell, "train", builder)
            if not self.replicate:
                self.ledger.append({"kind": "train", "test_id": res.test_id, **res.row()})
            out[res.test_id] = res
        self.results["train"].update(out)
        return out

    def open_holdout(self, cell_id: str, builder: Builder) -> CellResult:
        cell = self._cell(cell_id)
        tid = self.spec.test_id(cell.id)
        st = self.ledger.tests()[tid]
        if self.replicate:
            if st.holdout_open is None:
                raise HoldoutError(
                    f"{tid}: replicate cannot open a holdout the original study never opened"
                )
        else:
            if st.train is None:
                raise HoldoutError(f"{tid}: no holdout before the train verdict is recorded")
            if not st.train.get("passed"):
                raise HoldoutError(f"{tid}: failed train; use record_holdout() for the record")
            if st.holdout_open is not None:
                raise HoldoutError(f"{tid}: holdout already opened (at most once per cell)")
            if self.ledger.is_committed(st.train["seq"]) is False:
                raise HoldoutError(
                    f"{tid}: commit the ledger with the train verdicts before opening any holdout "
                    f"(git commit -m 'docs(research): {self.spec.round} train verdicts' "
                    f"-- {DEFAULT_LEDGER})"
                )
            self.ledger.append({"kind": "holdout_open", "test_id": tid})  # spent before computing
        res = self._run(cell, "holdout", builder)
        if not self.replicate:
            self.ledger.append(
                {"kind": "holdout", "test_id": tid, "record_only": False, **res.row()}
            )
        self.results["holdout"][tid] = res
        return res

    def _forward_sample(
        self, cell: Cell, builder: Builder, now: datetime
    ) -> tuple[list[Episode], int, int]:
        """(the first forward_n episodes by signal time, unpriced closed ones
        skipped and counted, closed so far). An episode still open at `now`
        holds its place in the sample whatever its net says (a builder cannot
        price it yet). Complete when all forward_n have closed."""
        eps = self._build(cell, "holdout", builder, end=now)
        sample: list[Episode] = []
        unpriced = 0
        for e in eps:
            if len(sample) == self.spec.forward_n:
                break
            if e.exit_time > now or math.isfinite(e.net_bps):
                sample.append(e)
            else:
                unpriced += 1
        closed = sum(1 for e in sample if e.exit_time <= now)
        return sample, unpriced, closed

    def forward_status(self, cell_id: str, builder: Builder, now: datetime | None = None) -> dict:
        """How far a forward test is: episodes closed of forward_n. Counts only,
        never a return; writes nothing."""
        if not self.spec.forward_n:
            raise HoldoutError("forward_status() is for forward tests (Spec.forward_n > 0)")
        now = now or datetime.now(UTC)
        sample, unpriced, closed = self._forward_sample(self._cell(cell_id), builder, now)
        return {"test_id": self.spec.test_id(self._cell(cell_id).id), "closed": closed,
                "need": self.spec.forward_n, "unpriced": unpriced,
                "due": closed == self.spec.forward_n}

    def open_forward(self, cell_id: str, builder: Builder, now: datetime | None = None) -> CellResult:
        """The forward test's single decision, on the first forward_n episodes.
        Refused (nothing written) until all of them have closed; then
        `holdout_open` is written before the statistics are computed."""
        if not self.spec.forward_n:
            raise HoldoutError("open_forward() is for forward tests (Spec.forward_n > 0)")
        cell = self._cell(cell_id)
        tid = self.spec.test_id(cell.id)
        st = self.ledger.tests()[tid]
        if self.replicate and st.holdout_open is None:
            raise HoldoutError(f"{tid}: replicate cannot open a forward test the original never opened")
        if not self.replicate and st.holdout_open is not None:
            raise HoldoutError(f"{tid}: forward test already decided (at most once)")
        now = now or datetime.now(UTC)
        sample, unpriced, closed = self._forward_sample(cell, builder, now)
        if closed < self.spec.forward_n:
            raise HoldoutError(
                f"{tid}: {closed} of {self.spec.forward_n} forward episodes closed; "
                "the forward test is decided only at n"
            )
        if not self.replicate:
            self.ledger.append({"kind": "holdout_open", "test_id": tid, "forward_n": self.spec.forward_n})
        res = self._result(cell, "holdout", sample, unpriced)
        if not self.replicate:
            self.ledger.append(
                {"kind": "holdout", "test_id": tid, "record_only": False,
                 "forward_n": self.spec.forward_n, **res.row()}
            )
        self.results["holdout"][tid] = res
        return res

    def record_holdout(self, cell_id: str, builder: Builder) -> CellResult:
        """Holdout of a train FAILURE, for the record only (never in q). Allowed
        once every cell of the study has its train verdict, so it cannot steer
        which cells are sent to the holdout."""
        cell = self._cell(cell_id)
        tid = self.spec.test_id(cell.id)
        tests = self.ledger.tests()
        missing = [c.id for c in self.spec.cells if tests[self.spec.test_id(c.id)].train is None]
        if missing:
            raise HoldoutError(
                f"record-only holdouts wait for every train verdict; missing {missing}"
            )
        st = tests[tid]
        if st.train.get("passed"):
            raise HoldoutError(
                f"{tid} passed train; its holdout is a decision — use open_holdout()"
            )
        if not self.replicate and st.record is not None:
            raise HoldoutError(f"{tid}: record-only holdout already written")
        res = self._run(cell, "holdout", builder)
        if not self.replicate:
            self.ledger.append(
                {"kind": "holdout", "test_id": tid, "record_only": True, **res.row()}
            )
        self.results["record"][tid] = res
        return res

    def finalise(self) -> list[dict]:
        """Final verdict per cell, with q from the whole ledger. A replicate
        returns the comparison instead and writes nothing."""
        tests = self.ledger.tests()
        q = self.ledger.qvalues()
        m = self.ledger.m
        out = []
        rows = []
        for cell in self.spec.cells:
            tid = self.spec.test_id(cell.id)
            st = tests[tid]
            if self.spec.forward_n:
                if st.holdout is None:
                    raise ProtocolError(f"{tid}: forward test not decided yet (open_forward at n)")
                verdict = (
                    "survives"
                    if st.holdout.get("passed") and q[tid] <= self.spec.q_max
                    else "rejected_forward"
                )
                row = {"kind": "final", "test_id": tid, "verdict": verdict, "q": q[tid], "m": m,
                       "p_family": st.family_p}
                out.append(row)
                if not self.replicate and st.final is None:
                    rows.append(row)
                continue
            if st.train is None:
                raise ProtocolError(f"{tid}: no train verdict")
            if st.train.get("passed") and st.holdout is None:
                raise ProtocolError(f"{tid}: passed train; open its holdout before finalising")
            if not st.train.get("passed"):
                verdict = "rejected_train"
            elif st.holdout.get("passed") and q[tid] <= self.spec.q_max:
                verdict = "survives"
            else:
                verdict = "rejected_holdout"
            row = {
                "kind": "final",
                "test_id": tid,
                "verdict": verdict,
                "q": q[tid],
                "m": m,
                "p_family": st.family_p,
            }
            out.append(row)
            if not self.replicate and st.final is None:
                rows.append(row)
        if rows:
            self.ledger.append(rows)
        return out


def register(
    spec: Spec,
    prereg: str,
    *,
    ledger: Ledger | None = None,
    git: SubprocessGit | None = None,
    root: str | Path | None = None,
    legacy: bool = False,
) -> Study:
    """Write the pre-registration (first call) or verify it and open the study.

    `prereg` is relative to the repository root. First call: the file is
    written and `NotCommitted` tells you to commit it alone. Later calls check
    the commit and the spec block, then register every cell in the ledger.

    If every cell is already in the ledger with identical params (same
    pre-registration commit), the study is a *replicate*: it recomputes, it
    never writes. `legacy=True` admits a hand-written pre-registration without
    a spec block (rounds 1-3b) — replicate only.
    """
    root = Path(root) if root else repo_root()
    git = git or SubprocessGit(root)
    ledger = ledger or Ledger(root / DEFAULT_LEDGER, git=git, repo_path=DEFAULT_LEDGER)
    path = root / prereg
    if not path.exists():
        if legacy:
            raise NotCommitted(f"{prereg} does not exist")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_prereg(spec))
        raise NotCommitted(
            f"wrote {prereg}; commit it alone (git add {prereg} && git commit "
            f"-m 'docs: pre-register {spec.round}' -- {prereg}) and call register() again"
        )
    commit = git.adding_commit(prereg)
    if commit is None:
        raise NotCommitted(f"{prereg} is not committed (git log -- {prereg} is empty)")
    others = [f for f in git.files_in(commit) if f != prereg]
    if others:
        raise NotCommitted(
            f"{prereg} was committed in {commit[:8]} together with {others}; it must be alone"
        )
    tests = ledger.tests()
    known = {spec.test_id(c.id): tests.get(spec.test_id(c.id)) for c in spec.cells}
    for c in spec.cells:
        st = known[spec.test_id(c.id)]
        if st is not None and st.register.get("params") != json.loads(json.dumps(dict(c.params))):
            raise FrozenCellError(
                f"{spec.test_id(c.id)} is frozen with params {st.register.get('params')}; "
                f"got {dict(c.params)} — a changed rule is a new cell id"
            )
    present = [t for t, st in known.items() if st is not None]
    if present and len(present) != len(known):
        raise FrozenCellError(
            f"cells {present} already exist; a study cannot mix new and existing cells"
        )
    if legacy:
        if not present:
            raise SpecMismatch(
                "legacy pre-registrations can only replicate cells already in the ledger"
            )
    else:
        committed = git.blob_at(commit, prereg)
        block = _spec_block(committed.decode()) if committed else None
        if block is None:
            raise SpecMismatch(f"{prereg} at {commit[:8]} has no harness-spec block")
        if block != json.loads(spec.canonical()):
            raise SpecMismatch(f"the spec differs from the one committed in {commit[:8]}")
        now = _spec_block(path.read_text())
        if now != block:
            raise SpecMismatch(f"{prereg}: the spec block was edited after {commit[:8]}")
    if present:
        commits = {known[t].register.get("prereg_commit") for t in present}
        if commits != {commit}:
            raise FrozenCellError(
                f"cells were registered under pre-registration {commits}, not {commit[:8]}"
            )
        return Study(spec, prereg, commit, ledger, replicate=True)
    try:
        ledger.append(
            [
                {
                    "kind": "register",
                    "test_id": spec.test_id(c.id),
                    "round": spec.round,
                    "family": spec.family,
                    "title": spec.title,
                    "params": dict(c.params),
                    "prereg": prereg,
                    "prereg_commit": commit,
                    "spec_hash": spec.hash(),
                    "train_window": list(spec.train),
                    "holdout_window": list(spec.holdout),
                    "cluster": spec.cluster,
                    "min_t": spec.min_t,
                    **({"forward_n": spec.forward_n} if spec.forward_n else {}),
                }
                for c in spec.cells
            ]
        )
    except LedgerError as e:
        raise ProtocolError(str(e)) from e
    return Study(spec, prereg, commit, ledger, replicate=False)

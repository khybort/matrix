"""The research ledger: every hypothesis ever tested, append-only, committed.

One JSON object per line (`docs/research/ledger.jsonl`). The ledger is the
only place the programme's multiple-testing count m and the cumulative
Benjamini-Yekutieli q-values come from. A round that counts its own m by hand
(the 2026-10 rounds counted 38, 29, 18 and 6 differently) cannot be compared
with the next one; the ledger can.

Row kinds, per test (`test_id` = "<round>.<cell>"):
  register      -> params, prereg path + commit, spec hash        (exactly once)
  train         -> n, mean, t, clusters, p, passed               (exactly once)
  holdout_open  -> written BEFORE the holdout is computed         (once; train passers only)
  holdout       -> decision holdout result                        (once; after holdout_open)
                   or record_only=true for a train failure        (once; never enters q)
  final         -> verdict, q and m at that moment                (once)
  note          -> free text (e.g. an adversarial check that revised a result)

Tamper evidence: each row carries `prev`, the sha256 of the previous line, and
`seq`. `verify()` walks the chain; `append()` refuses to write when the chain
is broken or when the file no longer starts with the version committed at
HEAD (a rewritten history is visible to git, and refused here).

Family p for BHY: the one-sided p of the decision holdout when the test had
one; 1.0 otherwise (a train failure never rejects its null). m counts every
registered test, including train failures — that is the price of looking.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from matrix_shared.research.stats import bhy_qvalues

UTC = timezone.utc  # noqa: UP017 — datetime.UTC is 3.11+, the host python (pandas) is 3.9

KINDS = ("register", "train", "holdout_open", "holdout", "final", "note")


class LedgerError(RuntimeError):
    """A write the protocol forbids (order, duplicate, tampering)."""


def _canon(row: dict) -> str:
    return json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha(line: str) -> str:
    return hashlib.sha256(line.encode()).hexdigest()


@dataclass
class TestState:
    test_id: str
    register: dict
    train: dict | None = None
    holdout_open: dict | None = None
    holdout: dict | None = None  # decision holdout
    record: dict | None = None  # record-only holdout of a train failure
    final: dict | None = None
    notes: list[dict] = field(default_factory=list)

    @property
    def family_p(self) -> float:
        if self.holdout is not None and self.holdout.get("p") is not None:
            return float(self.holdout["p"])
        return 1.0


class Ledger:
    def __init__(self, path: str | Path, *, git=None, repo_path: str | None = None) -> None:
        """`git` (see protocol.SubprocessGit) enables the committed-prefix check;
        `repo_path` is the ledger's path relative to the repository root."""
        self.path = Path(path)
        self.git = git
        self.repo_path = repo_path

    # ---- reading ---------------------------------------------------------
    def lines(self) -> list[str]:
        if not self.path.exists():
            return []
        return [ln for ln in self.path.read_text().splitlines() if ln.strip()]

    def rows(self) -> list[dict]:
        return [json.loads(ln) for ln in self.lines()]

    def verify(self) -> None:
        prev = ""
        for i, ln in enumerate(self.lines()):
            row = json.loads(ln)
            if row.get("seq") != i:
                raise LedgerError(f"line {i + 1}: seq {row.get('seq')} != {i}")
            if row.get("prev") != prev:
                raise LedgerError(
                    f"line {i + 1}: hash chain broken (row edited, removed or reordered)"
                )
            if _canon(row) != ln:
                raise LedgerError(f"line {i + 1}: not in canonical form (hand-edited?)")
            prev = _sha(ln)

    def tests(self) -> dict[str, TestState]:
        out: dict[str, TestState] = {}
        for r in self.rows():
            if r["kind"] == "register" or r.get("test_id") in out:
                _apply(out, r)
        return out

    @property
    def m(self) -> int:
        return len(self.tests())

    def qvalues(self) -> dict[str, float]:
        """Cumulative BHY q for every registered test, from the ledger alone."""
        ts = self.tests()
        ids = list(ts)
        q = bhy_qvalues([ts[i].family_p for i in ids], m=len(ids))
        return dict(zip(ids, q))  # noqa: B905 (3.9 host)

    def is_committed(self, seq: int) -> bool | None:
        """Whether row `seq` is in the version committed at HEAD; None without git."""
        if self.git is None or self.repo_path is None:
            return None
        head = self.git.head_blob(self.repo_path) or b""
        return seq < sum(1 for ln in head.decode().splitlines() if ln.strip())

    # ---- writing ---------------------------------------------------------
    def _check_committed_prefix(self, current: bytes) -> None:
        if self.git is None or self.repo_path is None:
            return
        head = self.git.head_blob(self.repo_path)
        if head is not None and not current.startswith(head):
            raise LedgerError(
                f"{self.repo_path} no longer starts with its committed version: committed rows "
                "were edited or removed. The ledger is append-only; restore it from git."
            )

    def _check_order(self, row: dict, tests: dict[str, TestState]) -> None:
        kind, tid = row["kind"], row.get("test_id")
        if kind not in KINDS:
            raise LedgerError(f"unknown row kind {kind!r}")
        if kind == "note" and tid is None:
            return
        st = tests.get(tid)
        if kind == "register":
            if st is not None:
                raise LedgerError(f"{tid} is already registered")
            return
        if st is None:
            raise LedgerError(f"{tid} is not registered")
        if kind == "train":
            if st.train is not None:
                raise LedgerError(f"{tid}: train verdict already recorded (frozen)")
        elif kind == "holdout_open":
            if st.train is None:
                raise LedgerError(f"{tid}: no holdout before the train decision is recorded")
            if not st.train.get("passed"):
                raise LedgerError(f"{tid}: failed train; its holdout cannot decide anything")
            if st.holdout_open is not None:
                raise LedgerError(f"{tid}: holdout already opened once")
        elif kind == "holdout":
            if row.get("record_only"):
                if st.train is None or st.train.get("passed"):
                    raise LedgerError(f"{tid}: record-only holdouts are for train failures")
                if st.record is not None:
                    raise LedgerError(f"{tid}: record-only holdout already written")
            else:
                if st.holdout_open is None:
                    raise LedgerError(f"{tid}: holdout result without holdout_open")
                if st.holdout is not None:
                    raise LedgerError(f"{tid}: holdout already decided")
        elif kind == "final":
            if st.train is None:
                raise LedgerError(f"{tid}: no final verdict before train")
            if st.train.get("passed") and st.holdout is None:
                raise LedgerError(f"{tid}: train passer needs its holdout before a final verdict")
            if st.final is not None:
                raise LedgerError(f"{tid}: final verdict already written")

    def append(self, rows: list[dict] | dict) -> list[dict]:
        rows = [rows] if isinstance(rows, dict) else list(rows)
        current = self.path.read_bytes() if self.path.exists() else b""
        self._check_committed_prefix(current)
        self.verify()
        lines = self.lines()
        tests = self.tests()
        prev = _sha(lines[-1]) if lines else ""
        seq = len(lines)
        out, written = [], []
        for r in rows:
            r = {k: v for k, v in r.items() if k not in ("seq", "prev")}
            r.setdefault("at", datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))
            self._check_order(r, tests)
            r["seq"], r["prev"] = seq, prev
            ln = _canon(r)
            out.append(ln)
            written.append(r)
            # keep the in-memory view current for the next row's order check
            _apply(tests, r)
            prev, seq = _sha(ln), seq + 1
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as fh:
            if current and not current.endswith(b"\n"):
                fh.write("\n")
            fh.write("".join(ln + "\n" for ln in out))
        return written

    def __iter__(self) -> Iterator[dict]:
        return iter(self.rows())


def _apply(tests: dict[str, TestState], r: dict) -> None:
    kind, tid = r["kind"], r.get("test_id")
    if kind == "note" and tid is None:
        return
    if kind == "register":
        tests[tid] = TestState(tid, r)
        return
    st = tests[tid]
    if kind == "train":
        st.train = r
    elif kind == "holdout_open":
        st.holdout_open = r
    elif kind == "holdout":
        if r.get("record_only"):
            st.record = r
        else:
            st.holdout = r
    elif kind == "final":
        st.final = r
    elif kind == "note":
        st.notes.append(r)

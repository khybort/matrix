"""Meta-labeling: a second model that decides *whether to act* on a signal.

López de Prado's construction. The primary model (a strategy) decides the
side; a secondary model, trained on the primary's own history, predicts the
probability that acting on this particular signal ends profitably after costs.
Its output sizes or vetoes the bet — it never flips the side.

It fits this system exactly. Only ~12% of predictions can be filled (slots,
cash, freshness), so "which of these do we take" is the highest-leverage
decision left, and the current answer — the EV ranker — was measured to add
nothing (traded −15.0 bps vs skipped −13.9 bps, 2026-09-13).

Labels come from the triple barrier replayed on 1m bars, so every signal has
one whether or not capital was committed. Features come from the feature
snapshot each prediction already carries. The model is a plain L2 logistic
regression trained by gradient descent: with a few hundred samples per
strategy, anything heavier would fit noise, and this keeps the runtime free of
native dependencies.

Honesty rules baked in:
  * time-ordered split with an embargo of one horizon between train and test,
    because overlapping trades leak (Advances in Financial Machine Learning,
    ch. 7);
  * the reported metric is decision-relevant — mean net bps of the signals the
    model would take, out of sample, against taking them all;
  * a model is only written to disk when that lift is positive.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from loguru import logger

from matrix_shared.edge_study import (
    Bar,
    _index_at,
    _load_bars,
    _load_candidates,
    simulate_bracket,
)
from matrix_shared.setup_memory import setup_vector
from matrix_shared.trading import execution_cost_bps

MODEL_DIR = Path(os.environ.get("MATRIX_MODEL_DIR") or
                 os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"),
                              "matrix_models"))
MIN_SAMPLES = int(os.environ.get("MATRIX_META_MIN_SAMPLES", "200"))
TEST_FRACTION = float(os.environ.get("MATRIX_META_TEST_FRACTION", "0.3"))
EPOCHS = int(os.environ.get("MATRIX_META_EPOCHS", "400"))
LR = float(os.environ.get("MATRIX_META_LR", "0.35"))
L2 = float(os.environ.get("MATRIX_META_L2", "0.01"))
TAKE_FRACTION = float(os.environ.get("MATRIX_META_TAKE_FRACTION", "0.5"))
# A model only earns the right to gate capital if its out-of-sample lift is
# large enough to matter against the round trip and its ranking is better than
# a coin flip by a visible margin. Measured 2026-09-20, the best lift on this
# book was +2.9 bps at AUC 0.47 — noise. Without these floors the study would
# have shipped three such models.
MIN_LIFT_BPS = float(os.environ.get("MATRIX_META_MIN_LIFT_BPS", "5"))
MIN_AUC = float(os.environ.get("MATRIX_META_MIN_AUC", "0.55"))
_BPS = 10_000.0


def featurize(features: dict, *, side: str, confidence: float) -> list[float]:
    """Setup vector (flow, book, funding, OI, regime, graph) plus the two facts
    the primary model contributes: its direction and its own confidence."""
    return [*setup_vector(features or {}), 1.0 if side != "short" else -1.0,
            max(0.0, min(1.0, confidence)) * 2 - 1.0]


def sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def fit_logistic(
    xs: list[list[float]], ys: list[int], *, epochs: int = EPOCHS, lr: float = LR, l2: float = L2
) -> list[float]:
    """L2 logistic regression by full-batch gradient descent. Returns weights
    with the bias in position 0."""
    if not xs:
        return []
    dim = len(xs[0]) + 1
    w = [0.0] * dim
    n = len(xs)
    for _ in range(epochs):
        grad = [0.0] * dim
        for x, y in zip(xs, ys):
            z = w[0] + sum(wi * xi for wi, xi in zip(w[1:], x))
            err = sigmoid(z) - y
            grad[0] += err
            for j, xi in enumerate(x):
                grad[j + 1] += err * xi
        w[0] -= lr * grad[0] / n
        for j in range(1, dim):
            w[j] -= lr * (grad[j] / n + l2 * w[j])
    return w


def predict_proba(w: list[float], x: list[float]) -> float:
    if not w:
        return 0.5
    return sigmoid(w[0] + sum(wi * xi for wi, xi in zip(w[1:], x)))


def auc(scores: list[float], labels: list[int]) -> float:
    """Rank-based AUC; 0.5 means the model knows nothing."""
    pairs = sorted(zip(scores, labels))
    pos = sum(labels)
    neg = len(labels) - pos
    if pos == 0 or neg == 0:
        return 0.5
    rank_sum, i = 0.0, 0
    while i < len(pairs):
        j = i
        while j + 1 < len(pairs) and pairs[j + 1][0] == pairs[i][0]:
            j += 1
        avg_rank = (i + j) / 2 + 1
        for k in range(i, j + 1):
            if pairs[k][1] == 1:
                rank_sum += avg_rank
        i = j + 1
    return (rank_sum - pos * (pos + 1) / 2) / (pos * neg)


@dataclass
class MetaEvaluation:
    strategy_id: str
    asset_class: str
    n_train: int = 0
    n_test: int = 0
    auc: float = 0.5
    base_net_bps: float = 0.0      # taking every signal, out of sample
    model_net_bps: float = 0.0     # taking only what the model would take
    take_rate: float = 0.0
    threshold: float = 0.5
    weights: list[float] = field(default_factory=list)

    @property
    def lift_bps(self) -> float:
        return self.model_net_bps - self.base_net_bps

    def as_row(self) -> dict:
        return {
            "strategy": self.strategy_id, "market": self.asset_class,
            "n_train": self.n_train, "n_test": self.n_test,
            "auc": round(self.auc, 3), "base_net_bps": round(self.base_net_bps, 1),
            "model_net_bps": round(self.model_net_bps, 1), "lift_bps": round(self.lift_bps, 1),
            "take_rate": round(self.take_rate, 2), "threshold": round(self.threshold, 4),
            "useful": bool(
                self.lift_bps >= MIN_LIFT_BPS and self.auc >= MIN_AUC and self.n_test >= 50
            ),
        }


def split_with_embargo(n: int, test_fraction: float, embargo: int) -> tuple[range, range]:
    """Time-ordered split leaving `embargo` samples unused between the two, so
    a trade whose horizon spans the boundary cannot leak into training."""
    n_test = max(1, int(n * test_fraction))
    test_start = n - n_test
    train_end = max(0, test_start - embargo)
    return range(0, train_end), range(test_start, n)


def evaluate_samples(
    xs: list[list[float]], nets: list[float], *, test_fraction: float = TEST_FRACTION,
    embargo: int = 5, take_fraction: float = TAKE_FRACTION,
) -> MetaEvaluation:
    """Train on the earlier part, judge on the later part. `nets` are net bps."""
    ev = MetaEvaluation("", "")
    ys = [1 if v > 0 else 0 for v in nets]
    tr, te = split_with_embargo(len(xs), test_fraction, embargo)
    ev.n_train, ev.n_test = len(tr), len(te)
    if ev.n_train < 50 or ev.n_test < 20 or len(set(ys[tr.start:tr.stop])) < 2:
        return ev
    w = fit_logistic([xs[i] for i in tr], [ys[i] for i in tr])
    ev.weights = w
    test_scores = [predict_proba(w, xs[i]) for i in te]
    test_nets = [nets[i] for i in te]
    ev.auc = auc(test_scores, [ys[i] for i in te])
    ev.base_net_bps = sum(test_nets) / len(test_nets)
    # Threshold from the TRAIN distribution only — choosing it on test would be
    # the selection bias this whole module exists to avoid.
    train_scores = sorted(predict_proba(w, xs[i]) for i in tr)
    ev.threshold = train_scores[int(len(train_scores) * (1 - take_fraction))]
    taken = [net for sc, net in zip(test_scores, test_nets) if sc >= ev.threshold]
    ev.take_rate = len(taken) / len(test_nets)
    ev.model_net_bps = sum(taken) / len(taken) if taken else 0.0
    return ev


async def build_samples(days: float, strategy_id: str | None) -> dict[tuple[str, str], tuple[list, list]]:
    """(features, net bps) per strategy, labelled by replaying the triple
    barrier on 1m bars — no fill required."""
    rows = await _load_candidates(days, strategy_id)
    if not rows:
        return {}
    since = datetime.now(UTC) - timedelta(days=days + 1)
    by_class: dict[str, set[str]] = {}
    for r in rows:
        by_class.setdefault(r["asset_class"], set()).add(r["symbol"])
    bars: dict[tuple[str, str], list[Bar]] = {}
    for ac, syms in by_class.items():
        for sym, b in (await _load_bars(syms, ac, since)).items():
            bars[(ac, sym)] = b

    out: dict[tuple[str, str], tuple[list, list]] = {}
    async with __import__("matrix_shared.db", fromlist=["shared_session_scope"]).shared_session_scope() as s:
        from sqlalchemy import text as _t
        ctx_rows = (await s.execute(_t(
            "SELECT p.generated_at, p.symbol, p.strategy_id, p.confidence, p.context->'features' AS feats "
            "FROM predictions p WHERE p.generated_at >= now() - make_interval(secs => :secs) "
            "AND p.side IN ('long','short')"
        ), {"secs": days * 86400})).mappings().all()
    ctx = {(c["strategy_id"], c["symbol"], c["generated_at"]): c for c in ctx_rows}

    for r in rows:
        series = bars.get((r["asset_class"], r["symbol"]))
        if not series:
            continue
        idx = _index_at(series, r["generated_at"])
        if idx <= 0 or idx >= len(series) - 1:
            continue
        horizon_bars = max(1, int((r["horizon_seconds"] or 600) // 60))
        tp = float(r["tp_pct"]) if r["tp_pct"] is not None else 0.01
        sl = float(r["sl_pct"]) if r["sl_pct"] is not None else 0.005
        sim = simulate_bracket(series, idx, side=r["side"], tp_pct=tp, sl_pct=sl, horizon_bars=horizon_bars)
        if sim.reason == "no_data":
            continue
        c = ctx.get((r["strategy_id"], r["symbol"], r["generated_at"]))
        feats = (c["feats"] if c and isinstance(c["feats"], dict) else {}) or {}
        conf = float(c["confidence"]) if c and c["confidence"] is not None else 0.5
        cost = float(execution_cost_bps(r["asset_class"], r["symbol"])) * 2
        key = (r["strategy_id"], r["asset_class"])
        xs, nets = out.setdefault(key, ([], []))
        xs.append(featurize(feats, side=r["side"], confidence=conf))
        nets.append(sim.ret_bps - cost)
    return out


async def run_meta_study(*, days: float = 14.0, strategy_id: str | None = None) -> list[dict]:
    samples = await build_samples(days, strategy_id)
    rows = []
    for (sid, ac), (xs, nets) in samples.items():
        if len(xs) < MIN_SAMPLES:
            continue
        ev = evaluate_samples(xs, nets)
        ev.strategy_id, ev.asset_class = sid, ac
        rows.append(ev)
    rows.sort(key=lambda e: e.lift_bps, reverse=True)
    for ev in rows:
        if ev.as_row()["useful"]:
            save_model(ev)
    return [e.as_row() for e in rows]


def model_path(strategy_id: str, asset_class: str) -> Path:
    return MODEL_DIR / f"meta_{strategy_id}_{asset_class}.json"


def save_model(ev: MetaEvaluation) -> None:
    try:
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        model_path(ev.strategy_id, ev.asset_class).write_text(json.dumps({
            "strategy_id": ev.strategy_id, "asset_class": ev.asset_class,
            "weights": ev.weights, "threshold": ev.threshold,
            "auc": ev.auc, "lift_bps": ev.lift_bps, "n_train": ev.n_train,
            "trained_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }))
        logger.info(f"meta-label model saved for {ev.strategy_id}/{ev.asset_class} "
                    f"(auc={ev.auc:.3f}, lift={ev.lift_bps:+.1f} bps)")
    except OSError as e:
        logger.warning(f"meta-label model not saved: {e}")


def load_model(strategy_id: str, asset_class: str) -> dict | None:
    try:
        p = model_path(strategy_id, asset_class)
        return json.loads(p.read_text()) if p.exists() else None
    except (OSError, json.JSONDecodeError):
        return None


def format_report(rows: list[dict], *, days: float) -> str:
    if not rows:
        return "meta-label study: not enough samples yet"
    head = (
        f"Meta-labeling — should we act on this signal? last {days:g}d, out-of-sample\n"
        f"{'strategy':<24}{'mkt':<7}{'train':>7}{'test':>6}{'auc':>7}{'base':>8}{'model':>8}{'lift':>8}{'take':>7}  use\n"
    )
    lines = [
        f"{r['strategy']:<24}{r['market']:<7}{r['n_train']:>7}{r['n_test']:>6}{r['auc']:>7.3f}"
        f"{r['base_net_bps']:>8.1f}{r['model_net_bps']:>8.1f}{r['lift_bps']:>8.1f}{r['take_rate']:>7.2f}"
        f"  {'YES' if r['useful'] else ''}"
        for r in rows
    ]
    return head + "\n".join(lines) + (
        f"\nbase = net bps taking every signal; model = taking only the top-scored half. "
        f"A model is written to disk only when out-of-sample lift >= {MIN_LIFT_BPS:g} bps "
        f"and auc >= {MIN_AUC:g}; anything less is noise."
    )

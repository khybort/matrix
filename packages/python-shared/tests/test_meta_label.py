"""Meta-labeling must be able to learn a real pattern, must not claim skill on
noise, and must never judge itself on data it trained on."""

from __future__ import annotations

import random

from matrix_shared.meta_label import (
    auc,
    evaluate_samples,
    featurize,
    fit_logistic,
    predict_proba,
    sigmoid,
    split_with_embargo,
)


def test_sigmoid_is_stable_at_the_extremes():
    # underflow to exactly 0 is fine; what matters is no OverflowError
    assert sigmoid(-800) == 0.0
    assert sigmoid(800) == 1.0
    assert sigmoid(0.0) == 0.5
    assert 0.0 < sigmoid(-30) < 1e-12


def test_featurize_adds_side_and_confidence_to_the_setup_vector():
    x = featurize({"regime": "low/flat/neutral"}, side="long", confidence=1.0)
    y = featurize({"regime": "low/flat/neutral"}, side="short", confidence=0.0)
    assert len(x) == len(y) == 16
    assert x[-2] == 1.0 and y[-2] == -1.0        # side
    assert x[-1] == 1.0 and y[-1] == -1.0        # confidence, centred
    assert featurize(None, side="long", confidence=0.5)[-1] == 0.0


def test_logistic_learns_a_separable_rule():
    xs = [[1.0, 0.0], [0.9, 0.1], [0.95, -0.1], [-1.0, 0.0], [-0.9, 0.1], [-0.95, -0.2]] * 20
    ys = [1, 1, 1, 0, 0, 0] * 20
    w = fit_logistic(xs, ys, epochs=600)
    assert predict_proba(w, [1.0, 0.0]) > 0.7
    assert predict_proba(w, [-1.0, 0.0]) < 0.3
    assert fit_logistic([], []) == []


def test_auc_ranks_and_handles_degenerate_labels():
    assert auc([0.9, 0.8, 0.2, 0.1], [1, 1, 0, 0]) == 1.0
    assert auc([0.1, 0.2, 0.8, 0.9], [1, 1, 0, 0]) == 0.0
    assert abs(auc([0.5] * 4, [1, 0, 1, 0]) - 0.5) < 1e-9
    assert auc([0.9, 0.1], [1, 1]) == 0.5        # one class only


def test_embargo_leaves_a_gap_between_train_and_test():
    tr, te = split_with_embargo(100, 0.3, embargo=5)
    assert te.start == 70 and te.stop == 100
    assert tr.stop == 65                          # 5 samples dropped at the seam
    assert tr.start == 0


def test_evaluation_finds_lift_when_a_feature_really_predicts_profit():
    rng = random.Random(3)
    xs, nets = [], []
    for _ in range(600):
        good = rng.random() < 0.5
        signal = 1.0 if good else -1.0
        xs.append([signal + rng.gauss(0, 0.3), rng.gauss(0, 1)])
        nets.append(rng.gauss(40 if good else -40, 25))
    ev = evaluate_samples(xs, nets, embargo=5)
    assert ev.auc > 0.7
    assert ev.lift_bps > 10          # taking the top half beats taking everything
    assert 0.3 < ev.take_rate < 0.7
    assert ev.as_row()["useful"] is True


def test_evaluation_claims_nothing_on_pure_noise():
    rng = random.Random(11)
    xs = [[rng.gauss(0, 1), rng.gauss(0, 1)] for _ in range(600)]
    nets = [rng.gauss(-5, 30) for _ in range(600)]
    ev = evaluate_samples(xs, nets, embargo=5)
    assert abs(ev.auc - 0.5) < 0.12
    assert abs(ev.lift_bps) < 12     # no meaningful edge invented from noise


def test_too_few_samples_returns_an_empty_verdict():
    ev = evaluate_samples([[1.0]] * 30, [1.0] * 30)
    assert ev.n_train == 0 or ev.as_row()["useful"] is False


def test_a_tiny_lift_is_not_called_useful():
    from matrix_shared.meta_label import MetaEvaluation

    noise = MetaEvaluation("s", "crypto", n_train=500, n_test=200, auc=0.53,
                           base_net_bps=-13.1, model_net_bps=-12.2)
    assert round(noise.lift_bps, 1) == 0.9
    assert noise.as_row()["useful"] is False        # would have shipped before

    real = MetaEvaluation("s", "crypto", n_train=500, n_test=200, auc=0.62,
                          base_net_bps=-10.0, model_net_bps=+8.0)
    assert real.as_row()["useful"] is True

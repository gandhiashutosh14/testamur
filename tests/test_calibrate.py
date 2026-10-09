"""Learn then Test picks the lowest threshold it can certify, stops at the first it cannot, and refuses to certify
anything on too little evidence."""
import random

from testamur.calibrate import Labelled, accepted, evaluate, learn_then_test


def _rows(seed=0):
    rng = random.Random(seed)
    rows = [Labelled(rng.uniform(95, 100), rng.uniform(20, 60), True) for _ in range(900)]      # clear owners
    rows += [Labelled(rng.uniform(86, 90), rng.uniform(20, 60), False) for _ in range(30)]      # near twins
    return rows


def test_the_threshold_stops_above_the_near_twins():
    r = learn_then_test(_rows(), alpha=0.01, delta=0.05)
    assert r["threshold"] is not None and r["threshold"] > 88
    at = evaluate(_rows(), r["threshold"])
    assert at["wrong"] <= 0.01 * at["n"]
    assert not r["table"][-1]["safe"] and all(t["safe"] for t in r["table"][:-1])     # fixed-sequence: stop at first


def test_a_looser_alpha_certifies_a_lower_threshold():
    strict = learn_then_test(_rows(), alpha=0.01)["threshold"]
    loose = learn_then_test(_rows(), alpha=0.05)["threshold"]
    assert loose <= strict


def test_too_few_labels_certify_nothing():
    rows = [Labelled(99.0, 30.0, True) for _ in range(100)]       # no errors, but 100 < log(0.05)/log(0.99)
    assert learn_then_test(rows, alpha=0.01, delta=0.05)["threshold"] is None
    assert learn_then_test(rows * 3, alpha=0.01, delta=0.05)["threshold"] is not None


def test_the_rule_matches_the_matcher():
    assert accepted(Labelled(93, 80, True), 92) and not accepted(Labelled(93, 90, True), 92)   # margin 5
    assert accepted(Labelled(100, 96, True), 99.9)                                            # exact name wins

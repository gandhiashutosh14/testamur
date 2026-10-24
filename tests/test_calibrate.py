"""Learn then Test picks the lowest threshold it can certify, stops at the first it cannot, and refuses to certify
anything on too little evidence."""
import random

from testamur.calibrate import Labelled, accepted, evaluate, learn_then_test


def _rows(seed=0):
    rng = random.Random(seed)
    rows = [Labelled(100.0, rng.uniform(20, 60), True) for _ in range(800)]                    # exact legal names
    rows += [Labelled(rng.uniform(95, 100), rng.uniform(20, 60), True) for _ in range(100)]    # clear owners
    rows += [Labelled(rng.uniform(86, 90), rng.uniform(20, 60), False) for _ in range(30)]     # near twins
    return rows


def test_the_threshold_stops_above_the_near_twins():
    r = learn_then_test(_rows(), alpha=0.01, delta=0.05)
    assert r["threshold"] is not None and r["threshold"] > 88
    at = evaluate(_rows(), r["threshold"])
    assert at["wrong"] <= 0.01 * at["accepted"]                       # among the documents it attaches
    assert not r["table"][-1]["safe"] and all(t["safe"] for t in r["table"][:-1])     # fixed-sequence: stop at first


def test_a_looser_alpha_certifies_a_lower_threshold():
    strict = learn_then_test(_rows(), alpha=0.01)["threshold"]
    loose = learn_then_test(_rows(), alpha=0.05)["threshold"]
    assert loose <= strict


def test_too_few_labels_certify_nothing():
    rows = [Labelled(99.0, 30.0, True) for _ in range(100)]       # no errors, but 100 < log(0.05)/log(0.99)
    assert learn_then_test(rows, alpha=0.01, delta=0.05)["threshold"] is None
    assert learn_then_test(rows * 3, alpha=0.01, delta=0.05)["threshold"] is not None


def test_abstentions_do_not_dilute_the_certified_error_rate():
    """The external review's counterexample: 10 accepted, all wrong, and 9,990 abstentions. Divided by every labelled
    document the error rate is 0.1% and 1% would be certified; among accepted documents it is 100%, and nothing is."""
    rows = [Labelled(99.0, 30.0, False) for _ in range(10)] + [Labelled(50.0, 40.0, True) for _ in range(9_990)]
    grid = [99.0]
    assert learn_then_test(rows, alpha=0.01, grid=grid, risk="unconditional")["threshold"] == 99.0
    selective = learn_then_test(rows, alpha=0.01, grid=grid)
    assert selective["risk"] == "selective" and selective["threshold"] is None
    assert selective["table"][0]["selective_risk"] == 1.0 and selective["table"][0]["unconditional_risk"] == 0.001


def test_the_rule_matches_the_matcher():
    assert accepted(Labelled(93, 80, True), 92) and not accepted(Labelled(93, 90, True), 92)   # margin 5
    assert accepted(Labelled(100, 96, True), 99.9)                                            # exact name wins

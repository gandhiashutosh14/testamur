"""The holdout comparison's own correctness: the cases are well formed and name real checks; the rules' outputs are
deterministic and match the committed report; an agent arm's output follows the coordinator's recommendation with a
person's question first; and every fault forced on purpose is contained."""
import json
from pathlib import Path

import pytest

from testamur import evaluate as ev
from testamur.pack import load_pack

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "evals" / "holdout" / "cases.yaml"
COMMITTED = ROOT / "reports" / "holdout-eval.json"


@pytest.fixture(scope="module")
def holdout():
    return ev.load(CASES)


def test_every_case_names_a_check_of_its_requirement_and_says_why(holdout):
    pack = load_pack()
    assert len(holdout.cases) >= 20
    for c in holdout.cases:
        req = pack.requirements[c.target["requirement"]]
        assert c.target.get("check") is None or c.target["check"] in req.checks, c.id
        assert c.why and c.documents and set(c.truth) <= {"PASS", "FAIL", "person"}, c.id


def test_a_label_outside_the_three_is_refused(tmp_path):
    bad = tmp_path / "cases.yaml"
    bad.write_text(CASES.read_text(encoding="utf-8").replace("truth: [PASS]", "truth: [MAYBE]", 1),
                   encoding="utf-8")
    with pytest.raises(ValueError):
        ev.load(bad)


def test_the_rules_give_the_same_outputs_every_run_and_as_reported(holdout, tmp_path):
    first = ev.evaluate(holdout, tmp_path / "a", None, arms=("rules",))
    again = ev.evaluate(holdout, tmp_path / "b", None, arms=("rules",), shard=(1, 2))
    outputs = {c["case"]: c["outputs"]["rules"] for c in first["cases"]}
    assert {c["case"]: c["outputs"]["rules"] for c in again["cases"]}.items() <= outputs.items()
    if COMMITTED.exists():
        committed = json.loads(COMMITTED.read_text(encoding="utf-8"))
        if committed["set"] == holdout.digest:
            assert {c["case"]: c["outputs"]["rules"] for c in committed["cases"]} == outputs


def test_a_question_for_a_person_comes_before_a_proposal_and_a_proposal_before_the_rules():
    target = {"requirement": "authority", "check": "auth_covers_voting"}
    ask = {"requirement": "authority", "check": None, "action": "ask_person", "verdict": None}
    propose = {"requirement": "authority", "check": "auth_covers_voting", "action": "propose_verdict",
               "verdict": "PASS"}
    elsewhere = {"requirement": "authority", "check": "auth_current", "action": "propose_verdict", "verdict": "FAIL"}
    assert ev.agent_output({"recommendations": [propose, ask]}, target, "person") == "person"
    assert ev.agent_output({"recommendations": [propose]}, target, "person") == "PASS"
    assert ev.agent_output({"recommendations": [elsewhere]}, target, "person") == "person"
    assert ev.agent_output({"recommendations": []}, target, "FAIL") == "FAIL"
    both = [propose, {**propose, "verdict": "FAIL"}]
    assert ev.agent_output({"recommendations": both}, target, "PASS") == "person"


def test_every_fault_forced_on_purpose_is_contained(holdout, tmp_path):
    rows = ev.faults(holdout, tmp_path)
    assert len(rows) == 5 and all(r["held"] for r in rows), [r for r in rows if not r["held"]]


def test_the_summary_counts_a_pass_outside_the_truth_as_a_false_pass():
    merged = {"set": "x", "cases": [
        {"case": "a", "category": "k", "truth": ["person"], "outputs": {"rules": "PASS"}, "runs": {}},
        {"case": "b", "category": "k", "truth": ["PASS"], "outputs": {"rules": "FAIL"}, "runs": {}},
        {"case": "c", "category": "k", "truth": ["FAIL", "person"], "outputs": {"rules": "person"}, "runs": {}}]}
    s = ev.summarise(merged)["rules"]
    assert (s["success"], s["false_pass"], s["false_fail"], s["person"]) == (1, 1, 1, 1)

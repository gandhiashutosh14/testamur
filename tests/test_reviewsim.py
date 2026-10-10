"""The loop closes: a reviewer who knows the truth turns every "not sure" into the right status, and random audits
catch a planted fault and stop its false vouching."""
import pytest

from testamur.reviewsim import accuracy_scenario, queue_scenario
from testamur.synth.world import build_world


@pytest.fixture(scope="module")
def tiny_dir(tmp_path_factory):
    out = tmp_path_factory.mktemp("sim_world")
    build_world(seed=3, size="tiny", out=out, digital_only=True)
    return out


def test_working_the_whole_queue_makes_every_status_right_and_a_reindex_changes_nothing(tiny_dir, tmp_path):
    q = queue_scenario(tiny_dir, tmp_path / "q.db")
    assert q["before"]["vs_settled_truth"].get("deferred", 0) > 0          # there was work for people
    assert q["effort"].get("did_not_land", 0) == 0 and q["effort"].get("left", 0) == 0
    assert q["after"] == {"exact": q["pairs"]}
    assert q["changed_by_reindex"] == 0


def test_audits_catch_a_planted_fault_and_its_false_vouching_stops(tiny_dir, tmp_path):
    a = accuracy_scenario(tiny_dir, tmp_path / "f.db", fault="limitation-years", audit_rate=1.0)
    assert a["before"].get("false_vouching", 0) > 0                      # the fault makes the engine vouch wrongly
    hit = [t for t in a["tenants"] if t["false_pass_verdicts"]]
    assert hit and all(t["tripped_after"] for t in hit)
    assert a["after_audit"].get("false_vouching", 0) == 0
    assert a["final"] == {"exact": a["pairs"]}

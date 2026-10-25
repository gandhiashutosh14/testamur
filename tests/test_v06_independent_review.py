"""The v0.6 independent review's regressions (written against e469b45, where all seven failed), kept as written but
for two changes the fixes required, and the boundary cases added beside each as it was fixed.

Changes to the original seven: the committed report is read as UTF-8; the whitespace citation is refused by the agents'
contract now, so that test checks the contract and, with the contract bypassed, the coordinator."""
import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError
from scripted import APPROVAL, Agents, Crash, _conditional_authority, _grant_finding, _pair
from sqlalchemy import func, select

from testamur import agents
from testamur import evaluate as ev
from testamur.evidence import metrics as m
from testamur.evidence.answer import answer
from testamur.evidence.metrics import correct
from testamur.evidence.run import differences, in_sample, merge, with_timings
from testamur.evidence.schema import Passage, Task
from testamur.investigate import Investigations, sqlite_checkpointer
from testamur.investigate.contracts import Citation, EvidencePacket, Finding, VerificationReport
from testamur.investigate.coordinator import reconcile, validate_findings
from testamur.modeltier import Cassette, CassetteMiss, quote_holds
from testamur.store import CheckReview, Proposal

ROOT = Path(__file__).parents[1]


def _report():
    return json.loads((ROOT / "reports/evidence-eval.json").read_text(encoding="utf-8"))


def _row():
    return _report()["tasks"][0]


# ------------------------------------------------------------------------------------- the review's seven, as written

def test_replay_detects_changed_retrieval_and_citation_results():
    before = _row()
    after = deepcopy(before)
    after["ranks"] = {k: None for k in before["ranks"]}
    after["answer"]["supported"] = not before["answer"]["supported"]
    after["answer"]["valid_citations"] = 999
    assert differences([after], [before]), "replay accepted changed ranks and citation status"


def test_replay_rejects_duplicate_task_ids():
    row = _row()
    assert differences([row, deepcopy(row)], [row]), "duplicate task collapsed into a dict"


@pytest.mark.parametrize("prediction", [
    "The result is 20 dollars; 5 was the starting value.",
    "The answer is 5 dollars.",
])
def test_financial_score_rejects_incidental_number_or_explicit_wrong_scale(prediction):
    # The expected answer is 5 MILLION dollars. Neither answer states that quantity.
    result = correct("answer", "tatqa", True, ["5"], None, {"answer_type": "arithmetic", "scale": "million"},
                     prediction, None, False)
    assert not result, f"wrong answer was accepted: {prediction}"


def test_whitespace_is_not_a_verified_agent_citation(env):
    _, _, engine = env
    claim, doc = _conditional_authority(env)
    finding = _grant_finding(doc)
    finding["citations"][0]["quote"] = " " * 8
    with pytest.raises(ValidationError):                       # the contract refuses it ...
        Finding.model_validate(finding)
    cited = Citation.model_construct(document_id=doc, page=1, quote=" " * 8)
    bypassed = Finding.model_validate(_grant_finding(doc)).model_copy(update={"citations": [cited]})
    checked = validate_findings(engine, 1, claim, [bypassed])  # ... and so does the coordinator, if it got past
    assert checked.citations_valid == 0 and not checked.findings


def test_pair_requires_verifier_evidence_before_claiming_both_agree(env):
    _, _, engine = env
    claim, doc = _conditional_authority(env)
    packet = EvidencePacket.model_validate({"findings": [_grant_finding(doc)]})
    report = VerificationReport.model_validate({"reviews": [
        {"finding": 0, "assessment": "supported", "reason": "looks fine", "citations": []}]})
    recs, _ = reconcile(engine, 1, claim, packet, report, "pair")
    assert not any(r.action == "propose_verdict" and r.agreement == "both" for r in recs)


def test_workflow_recovery_keeps_committed_approval_outcome(env, tmp_path):
    _, store, engine = env
    claim, doc = _conditional_authority(env)
    saver, conn = sqlite_checkpointer(tmp_path / "review-checkpoints.sqlite")
    flow = Investigations(engine, Agents(*_pair(doc)), saver)
    try:
        run = flow.start(1, claim, "reviewer")
        rec = next(r for r in run["recommendations"] if r.get("proposal_id"))
        original_update = flow._update

        def fail_after_domain_commit(st, **values):
            if values.get("status") == "applied":
                raise Crash("domain approval committed; graph outcome not checkpointed")
            return original_update(st, **values)
        flow._update = fail_after_domain_commit
        with pytest.raises(Crash):
            flow.resume(1, run["run"], [{"proposal": rec["proposal_id"], "approve": True, "digest": rec["digest"]}],
                        by="reviewer")
        flow._update = original_update
        with store.tenant(1) as s:
            assert s.scalar(select(func.count()).select_from(CheckReview).where(
                CheckReview.proposal_id == rec["proposal_id"])) == 1
        recovered = flow.recover(1, run["run"])
        assert recovered["status"] == "applied", recovered["outcomes"]
    finally:
        conn.close()


# ----------------------------------------------------------------------------- citations: what a quote must be

@pytest.mark.parametrize("quote", [" " * 8, "\t\t\n\n    ", " 　 " * 3, "​" * 8, "........",
                                   "  --  --  ", "   ab   "])
def test_the_agents_contract_refuses_a_quote_that_is_not_text(quote):
    with pytest.raises(ValidationError):
        Citation(document_id=1, page=1, quote=quote)


def test_a_short_quote_that_is_text_still_holds_and_a_blank_one_never_does():
    page = "LOAN AGREEMENT\nThe Borrower shall repay the facility in twelve equal instalments."
    assert Citation(document_id=1, page=1, quote="LOAN AGREEMENT").quote == "LOAN AGREEMENT"
    assert quote_holds("LOAN AGREEMENT", page)
    assert quote_holds("shall repay the facility in twelve", page)
    assert not quote_holds("shall repay the facility in eleven", page)
    for blank in ("", " ", "\t\n", "　", "​​", ". ' \""):
        assert not quote_holds(blank, page), repr(blank)


def test_the_coordinator_names_an_empty_quote_and_keeps_the_finding_out(env):
    _, _, engine = env
    claim, doc = _conditional_authority(env)
    blank = Citation.model_construct(document_id=doc, page=1, quote=" \t\n" * 3)
    finding = Finding.model_validate(_grant_finding(doc)).model_copy(update={"citations": [blank]})
    checked = validate_findings(engine, 1, claim, [finding])
    assert not checked.findings and checked.dropped and checked.citations_valid == 0


# ------------------------------------------------------------------------ the verifier: agreement needs its own citation

def test_a_verifier_agreeing_with_a_citation_that_does_not_hold_leaves_the_finding_to_a_person(env):
    _, _, engine = env
    claim, doc = _conditional_authority(env)
    packet = EvidencePacket.model_validate({"findings": [_grant_finding(doc)]})
    report = VerificationReport.model_validate({"reviews": [
        {"finding": 0, "assessment": "supported", "reason": "the board approved it",
         "citations": [{"document_id": doc, "page": 1, "quote": "the board never approved this authority"}]}]})
    recs, stats = reconcile(engine, 1, claim, packet, report, "pair")
    assert not any(r.action == "propose_verdict" for r in recs)
    (rec,) = [r for r in recs if r.check == "auth_covers_voting"]
    assert rec.action == "ask_person" and rec.agreement == "unresolved"
    assert "without a citation that could be verified" in rec.reason
    assert stats["citations"] - stats["citations_valid"] == 1


def test_a_verifier_agreeing_with_its_own_citation_that_holds_still_agrees(env):
    _, _, engine = env
    claim, doc = _conditional_authority(env)
    packet = EvidencePacket.model_validate({"findings": [_grant_finding(doc)]})
    report = VerificationReport.model_validate({"reviews": [
        {"finding": 0, "assessment": "supported", "reason": "the approval is on page 1",
         "citations": [{"document_id": doc, "page": 1, "quote": APPROVAL[:60]}]}]})
    recs, _ = reconcile(engine, 1, claim, packet, report, "pair")
    assert any(r.action == "propose_verdict" and r.agreement == "both" for r in recs)


# ------------------------------------------------------------------- recovery: every crash window, one effect, true status

@pytest.fixture
def flows(env, tmp_path):
    made = []

    def make(model):
        saver, conn = sqlite_checkpointer(tmp_path / "checkpoints.sqlite")
        made.append(conn)
        return Investigations(env[2], model, saver)
    yield make
    for c in made:
        c.close()


def _waiting(env, flows):
    claim, doc = _conditional_authority(env)
    flow = flows(Agents(*_pair(doc)))
    run = flow.start(1, claim, "lead@firm")
    rec = next(r for r in run["recommendations"] if r.get("proposal_id"))
    return flow, run, rec, [{"proposal": rec["proposal_id"], "approve": True, "digest": rec["digest"]}]


def _effects(store, proposal):
    with store.tenant(1) as s:
        return s.scalar(select(func.count()).select_from(CheckReview).where(CheckReview.proposal_id == proposal))


def test_a_crash_before_the_decision_commits_applies_it_once_on_recovery(env, flows, monkeypatch):
    _, store, _ = env
    flow, run, rec, decision = _waiting(env, flows)
    real = agents.decide

    def dies_first(*a, **k):
        raise Crash("the process died before the decision's transaction")
    monkeypatch.setattr(agents, "decide", dies_first)
    with pytest.raises(Crash):
        flow.resume(1, run["run"], decision, by="reviewer@firm")
    assert _effects(store, rec["proposal_id"]) == 0
    monkeypatch.setattr(agents, "decide", real)
    done = flow.recover(1, run["run"])
    assert done["status"] == "applied" and _effects(store, rec["proposal_id"]) == 1


def test_a_crash_after_the_outcome_is_written_keeps_it(env, flows):
    _, store, _ = env
    flow, run, rec, decision = _waiting(env, flows)
    original = flow._update

    def dies_after_writing(st, **values):
        out = original(st, **values)
        if values.get("status") == "applied":
            raise Crash("the outcome is written; the checkpoint is not")
        return out
    flow._update = dies_after_writing
    with pytest.raises(Crash):
        flow.resume(1, run["run"], decision, by="reviewer@firm")
    flow._update = original
    done = flow.recover(1, run["run"])
    assert done["status"] == "applied" and _effects(store, rec["proposal_id"]) == 1
    assert not done["outcomes"][0]["outcome"].startswith("refused")


def test_recovery_after_one_of_two_decisions_finishes_the_other_and_reports_both(env, flows):
    world, store, engine = env
    flow, run, rec, decision = _waiting(env, flows)
    claim = run["claim"]
    second = agents.propose(engine, 1, "investigation-test", "check_verdict",
                            {"claim": claim, "requirement": "authority", "check": "auth_covers_voting",
                             "verdict": "FAIL"}, "a second proposal of the same run", origin="test:second")
    applied = agents.decide(engine, 1, rec["proposal_id"], "reviewer@firm", True, seen=rec["digest"])
    st = {"tenant_id": 1, "run_id": run["run"], "claim_id": claim,
          "decisions": [{**decision[0], "by": "reviewer@firm"},
                        {"proposal": second["proposal"], "approve": False, "digest": second["digest"],
                         "by": "reviewer@firm"}]}
    out = flow._apply(st)                               # the step again, after a crash between the two decisions
    outcomes = flow.status(1, run["run"])["outcomes"]
    assert out["status"] == "applied" and [o["outcome"] for o in outcomes] == [applied, "declined"]
    assert _effects(store, rec["proposal_id"]) == 1
    with store.tenant(1) as s:
        assert s.get(Proposal, second["proposal"]).status == "declined"


def test_recovery_does_not_take_another_reviewers_decision_as_its_own(env, flows):
    world, store, engine = env
    flow, run, rec, decision = _waiting(env, flows)
    agents.decide(engine, 1, rec["proposal_id"], "other@firm", True, seen=rec["digest"])
    out = flow._apply({"tenant_id": 1, "run_id": run["run"], "claim_id": run["claim"],
                       "decisions": [{**decision[0], "by": "reviewer@firm"}]})
    (outcome,) = flow.status(1, run["run"])["outcomes"]
    assert out["status"] == "rejected" and outcome["outcome"].startswith("refused")
    assert "other@firm" in outcome["outcome"] and _effects(store, rec["proposal_id"]) == 1


# --------------------------------------------------------------- financial scoring: the quantity the answer states

MILLION = {"answer_type": "arithmetic", "scale": "million"}
PERCENT = {"answer_type": "arithmetic", "scale": "percent"}
PLAIN = {"answer_type": "arithmetic", "scale": ""}


@pytest.mark.parametrize("prediction, gold, meta, right", [
    ("5", "5", MILLION, True),                                        # in the table's unit
    ("$5", "5", MILLION, True),
    ("$5 million", "5", MILLION, True),
    ("5,000,000", "5", MILLION, True),                                # written out in full
    ("5,000 thousand", "5", MILLION, True),
    ("$5m", "5", MILLION, True),
    ("5 billion", "5", MILLION, False),                               # wrong scale
    ("5 thousand", "5", MILLION, False),
    ("5%", "5", MILLION, False),                                      # wrong unit
    ("It is 5 million, not 20 million", "5", MILLION, False),        # two quantities: neither is asserted
    ("Not 20 million but 5 million", "5", MILLION, False),
    ("In 2019 it was 5 million", "5", MILLION, True),                 # a year is not a quantity
    ("Q4 revenue was 5 million", "5", MILLION, True),                 # nor the 4 of Q4
    ("FY2019: $5 million", "5", MILLION, True),
    ("(200)", "-200", PLAIN, True),                                   # accounting brackets
    ("-$200", "-200", PLAIN, True),
    ("−200", "-200", PLAIN, True),
    ("it fell by 200", "-200", PLAIN, False),                         # the sign is part of the answer
    ("12%", "12", PERCENT, True),
    ("12 percent", "12", PERCENT, True),
    ("12", "12", PERCENT, True),
    ("$12", "12", PERCENT, False),                                    # money is not a percentage
    ("12 million", "12", PERCENT, False),
    ("0.12", "12", PERCENT, False),
    ("4.34% (calculated as (577 - 553) / 553 * 100)", "4.34", PERCENT, False),    # the working states more
    ("5 dollars", "5", PLAIN, True),                                  # units, where the gold is in units
])
def test_a_computed_answer_is_the_one_quantity_it_states_in_the_gold_unit(prediction, gold, meta, right):
    assert correct("answer", "tatqa", True, [gold], None, meta, prediction, None, False) is right


@pytest.mark.parametrize("prediction, spans, scale, right", [
    ("43 and 118", ["(43)", "(118)"], "thousand", False),            # brackets are a sign
    ("-43 and -118", ["(43)", "(118)"], "thousand", True),
    ("(43) and (118)", ["(43)", "(118)"], "thousand", True),
    ("Sales: $30,303; purchases: $303,793", ["30,303", "303,793"], "", True),
    ("$30,303 and $303,793 and $1,000", ["30,303", "303,793"], "", False),        # another number
    ("2019 and 2018", ["2019", "2018"], "", True),                    # years, where the gold is years
    ("$1.1 billion", ["$1.1 billion"], "", True),
    ("1.1", ["$1.1 billion"], "", False),                             # the gold names its unit
    ("169", ["$169"], "thousand", True),
])
def test_a_gold_span_that_is_a_number_is_compared_as_one(prediction, spans, scale, right):
    meta = {"answer_type": "multi-span" if len(spans) > 1 else "span", "scale": scale, "spans": spans}
    assert correct("answer", "tatqa", True, [", ".join(spans)], None, meta, prediction, None, False) is right


def test_a_text_span_is_still_judged_by_its_words():
    meta = {"answer_type": "span", "scale": "", "spans": ["payroll and related benefits"]}
    assert correct("answer", "tatqa", True, ["payroll and related benefits"], None, meta,
                   "payroll and related benefits", None, False)
    assert m.spans_match("anything", ["payroll and related benefits"], "") is None


# ------------------------------------------------------------- the replay check: every field, each task once, the sample

FIELDS = [("ranks", "hybrid"), ("ranks", "bm25"), ("answer", "label"), ("answer", "abstained"), ("answer", "supported"),
          ("answer", "citations"), ("answer", "valid_citations"), ("answer", "error"), ("answer", "chars"),
          ("correct", "as_answered"), ("correct", "evidence_rule"), ("abstained_under_rule",), ("f1",),
          ("tokens", "prompt"), ("tokens", "completion"), ("judge", "calls"), ("judge", "tokens"),
          ("ragas", "skipped"), ("ragas", "scores", "faithfulness"), ("ragas", "scores", "answer_relevancy")]


def _set(row, path, value):
    target = row
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value


def _get(row, path):
    for key in path:
        row = row[key]
    return row


@pytest.mark.parametrize("path", FIELDS, ids=lambda p: ".".join(p))
def test_the_replay_check_sees_a_change_in_any_field_but_the_timings(path):
    before = next(r for r in _report()["tasks"] if r.get("ragas") and r["ragas"]["scores"].get("faithfulness")
                  is not None and r["ranks"].get("hybrid"))
    after = deepcopy(before)
    old = _get(before, path)
    new = {"x": "changed"} if isinstance(old, dict) else (
        not old if isinstance(old, bool) else ("changed" if isinstance(old, str) or old is None else old + 0.5))
    _set(after, path, new)
    assert differences([after], [before]) == [before["task"]]


def test_the_replay_check_ignores_timings_and_holds_answer_relevancy_to_its_tolerance():
    before = next(r for r in _report()["tasks"] if (r.get("ragas") or {}).get("scores", {}).get("answer_relevancy"))
    after = deepcopy(before)
    after["seconds"] = {"retrieve": 0.0, "answer": 0.0}
    after["judge"]["seconds"] = 0.0
    after["ragas"]["scores"]["answer_relevancy"] += 0.0009
    assert differences([after], [before]) == []
    after["ragas"]["scores"]["answer_relevancy"] += 0.002
    assert differences([after], [before]) == [before["task"]]


def test_the_replay_check_sees_a_task_missing_or_held_twice_on_either_side():
    rows = _report()["tasks"][:3]
    assert differences(rows[:2], rows) == [rows[2]["task"]]
    assert differences(rows, rows + [deepcopy(rows[0])]) == [rows[0]["task"]]


def test_results_that_do_not_belong_together_do_not_merge():
    report = _report()
    part = {k: report[k] for k in ("manifest", "commit", "generator", "judge", "embedder", "k")}
    a, b = {**part, "tasks": report["tasks"][:2]}, {**part, "tasks": report["tasks"][2:4]}
    assert len(merge([a, b])["tasks"]) == 4
    with pytest.raises(ValueError, match="more than one part"):
        merge([a, {**part, "tasks": report["tasks"][1:3]}])
    for key, other in (("embedder", "another-embedder"), ("k", 5), ("manifest", "0" * 16), ("commit", "abc1234")):
        with pytest.raises(ValueError):
            merge([a, {**b, key: other}])
    with pytest.raises(ValueError):
        merge([a, {**b, "generator": {**part["generator"], "fingerprint": "another"}}])


def test_results_must_be_the_committed_samples_tasks():
    report = _report()
    merged = {k: report[k] for k in ("manifest", "commit", "generator", "judge", "embedder", "k")} | {
        "tasks": report["tasks"]}
    in_sample(merged, ROOT / "evals/evidence/manifest.json", every=True)
    with pytest.raises(ValueError, match="missing"):
        in_sample({**merged, "tasks": report["tasks"][1:]}, ROOT / "evals/evidence/manifest.json", every=True)
    stray = {**report["tasks"][0], "task": "cuad:not-in-the-sample"}
    with pytest.raises(ValueError, match="not in the sample"):
        in_sample({**merged, "tasks": report["tasks"] + [stray]}, ROOT / "evals/evidence/manifest.json")
    with pytest.raises(ValueError, match="sample"):
        in_sample({**merged, "manifest": "0" * 16}, ROOT / "evals/evidence/manifest.json")


def test_a_rescored_replay_takes_the_recorded_runs_timings_and_nothing_else():
    report = _report()
    merged = {k: report[k] for k in ("manifest", "commit", "generator", "judge", "embedder", "k")} | {
        "tasks": [{**r, "seconds": {"retrieve": 0.0, "answer": 0.0}} for r in report["tasks"][:3]]}
    out = with_timings(merged, report)
    assert [r["seconds"] for r in out["tasks"]] == [r["seconds"] for r in report["tasks"][:3]]
    assert out["timings_from"] == report.get("timings_from", report["commit"])
    with pytest.raises(ValueError):
        with_timings({**merged, "generator": {**report["generator"], "model": "another"}}, report)
    with pytest.raises(ValueError):
        with_timings({**merged, "tasks": [{**report["tasks"][0], "task": "cuad:not-recorded"}]}, report)


def test_a_replay_missing_a_recorded_call_fails_instead_of_scoring_it(tmp_path):
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    task = Task("cuad:x", "cuad", "legal", "extract", "Assignment?", ["d"], ["clause"], evidence=["clause"])
    with pytest.raises(CassetteMiss):
        answer(Cassette(empty, model_id="recorded"), task, [Passage("d#0", "A clause about assignment.")])


def test_a_replay_missing_a_recorded_judgement_fails_instead_of_recording_an_error(tmp_path):
    pytest.importorskip("ragas")
    from testamur.evidence.judge import Judge
    from testamur.evidence.retrieve import HashEmbedder
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    judge = Judge(Cassette(empty, model_id="recorded"), HashEmbedder())
    with pytest.raises(CassetteMiss):
        judge.score("Is it assignable?", "It is not assignable.", ["The licence may not be assigned."],
                    "Not assignable.")


# ------------------------------------------------------------------------ the holdout's replay check, likewise

def _holdout():
    return json.loads((ROOT / "reports/holdout-eval.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("field, value", [("status", "applied"), ("citations_valid", -1), ("tokens", -1),
                                          ("steps", {"investigator": -1}), ("recommendations", []),
                                          ("tool_calls", -1), ("violations", 1)])
def test_the_holdout_check_sees_a_change_in_a_runs_path(field, value):
    case = next(c for c in _holdout()["cases"] if c["runs"].get("pair", {}).get("recommendations"))
    changed = deepcopy(case)
    changed["runs"]["pair"][field] = value
    assert ev.differences([changed], [case]) == [case["case"]]


def test_the_holdout_check_ignores_timings_and_sees_a_case_twice():
    cases = _holdout()["cases"][:2]
    slower = deepcopy(cases)
    for c in slower:
        for run in c["runs"].values():
            run["seconds"], run["agent_seconds"] = 0.0, {}
    assert ev.differences(slower, cases) == []
    assert ev.differences(cases + [deepcopy(cases[0])], cases) == [cases[0]["case"]]
    part = {k: v for k, v in _holdout().items() if k in ("set", "model", "fingerprint", "commit")}
    with pytest.raises(ValueError, match="more than one part"):
        ev.merge([{**part, "cases": cases}, {**part, "cases": cases[:1]}])


def test_a_rescored_holdout_takes_the_recorded_runs_timings():
    recorded = _holdout()
    replay = {**{k: recorded[k] for k in ("set", "model", "fingerprint", "commit")},
              "cases": [{**c, "runs": {a: {**r, "seconds": 0.0, "agent_seconds": {}} for a, r in c["runs"].items()}}
                        for c in recorded["cases"]]}
    out = ev.with_timings(replay, recorded)
    assert [c["runs"] for c in out["cases"]] == [c["runs"] for c in recorded["cases"]]
    with pytest.raises(ValueError):
        ev.with_timings({**replay, "set": "another"}, recorded)

"""The investigator-verifier workflow, driven by a scripted model so every path is exact: two agents in two contexts,
a coordinator that validates their answers and alone proposes, a person's approval applied once, checkpoints that
survive a crash during agent work and a restart during review, budgets, scope and stale evidence."""
import json
import random
from datetime import datetime, time, timedelta

import pytest
from sqlalchemy import func, select

from testamur.investigate import Budgets, Investigations, sqlite_checkpointer
from testamur.investigate.agent import Budget
from testamur.model import Status
from testamur.modeltier import Reply
from testamur.store import (
    AgentEvent,
    CheckReview,
    Claim,
    ClaimPermission,
    Creditor,
    Document,
    InvestigationRun,
    Proposal,
    RequirementState,
)
from testamur.synth.render import DocContent, render_pdf

APPROVAL = "The board of the creditor approved this authority on 14 August 2026 by resolution 12 of 2026."


class Crash(BaseException):
    """A process dying mid-call: not an error an agent handles."""


class Agents:
    """A scripted model for both agents. Each script is a list of turns (dicts, or callables of the messages); the
    last turn repeats when the script runs out. The role is read from the system prompt, as the model would see it."""
    model_id, fingerprint = "scripted-agents", "scripted"

    def __init__(self, investigator, verifier=None, crash_on=None):
        self.scripts = {"investigator": investigator, "verifier": verifier or []}
        self.calls = {"investigator": 0, "verifier": 0}
        self.crash_on = crash_on

    def chat(self, messages, max_tokens=200, schema=None):
        role = "verifier" if messages[0]["content"].startswith("You are the verifier") else "investigator"
        n = self.calls[role]
        self.calls[role] += 1
        if self.crash_on == (role, n):
            raise Crash()
        script = self.scripts[role]
        turn = script[min(n, len(script) - 1)]
        turn = turn(messages) if callable(turn) else turn
        return Reply(json.dumps(turn), 120, 40)


def call(tool, **args):
    return {"note": f"look at {tool}", "action": "call_tool", "tool": tool, "args": args}


def finish(result):
    return {"note": "done", "action": "finish", "result": result}


def _conditional_authority(env):
    """Give a claim a new current authority that grants the vote only subject to its board's approval, and records the
    approval: the rules abstain on the vote (they cannot see an approval), an agent can read it."""
    world, store, engine = env
    with store.tenant(1) as s:
        st = next(x for x in s.scalars(select(RequirementState).where(RequirementState.requirement_id == "authority",
                                                                      RequirementState.status == Status.SATISFIED,
                                                                      RequirementState.via_trustee.is_(False))))
        e = s.get(Claim, st.claim_id)
        name = s.get(Creditor, e.creditor_id).legal_name
    page = [("title", "LETTER OF AUTHORITY"), ("kv", "Financial creditor", name),
            ("kv", "Authorised representative", "Meera Iyer, Head of Recoveries"),
            ("kv", "Powers", "To vote at meetings of the committee of creditors, subject to the prior approval of the "
                             "board"),
            ("kv", "Effective from", world.as_of.isoformat()),
            ("kv", "Valid until", (world.as_of + timedelta(days=365)).isoformat()),
            ("kv", "Date of issue", world.as_of.isoformat()), ("para", APPROVAL)]
    now = datetime.combine(world.as_of, time(10))
    out = engine.process(1, engine.ingest(1, e.matter_id, "authority.pdf", render_pdf(DocContent([page]),
                                                                                      random.Random(9)),
                                          "creditor_portal", now, declared_creditor_id=e.creditor_id))
    _, results = engine.verdicts(1, e.id, "authority")
    assert next(r for r in results if r.check_id == "auth_covers_voting").verdict == "ABSTAIN"
    return e.id, out.document_id


@pytest.fixture
def flows(env, tmp_path):
    made = []

    def make(model, budgets=None):
        saver, conn = sqlite_checkpointer(tmp_path / "checkpoints.sqlite")
        made.append(conn)
        return Investigations(env[2], model, saver, budgets)
    yield make
    for c in made:
        c.close()


def _grant_finding(doc, stance="supports"):
    return {"requirement": "authority", "check": "auth_covers_voting", "stance": stance,
            "statement": "the vote is granted subject to board approval, and the letter records the approval",
            "citations": [{"document_id": doc, "page": 1, "quote": APPROVAL[:60]}]}


def _pair(doc, assessment="supported"):
    investigator = [call("claim_summary"), call("read_page", document_id=doc, page=1),
                    finish({"findings": [_grant_finding(doc)], "open_questions": []})]
    verifier = [call("read_page", document_id=doc, page=1),
                finish({"reviews": [{"finding": 0, "assessment": assessment, "reason": "the approval is on page 1",
                                     "citations": [{"document_id": doc, "page": 1, "quote": APPROVAL[:60]}]}],
                        "additional": []})]
    return investigator, verifier


def test_two_agents_agree_the_coordinator_proposes_and_a_person_applies_it_once(env, flows):
    world, store, engine = env
    claim, doc = _conditional_authority(env)
    model = Agents(*_pair(doc))
    run = flows(model).start(1, claim, "lead@firm")
    assert run["status"] == "awaiting_approval" and model.calls == {"investigator": 3, "verifier": 2}
    (rec,) = [r for r in run["recommendations"] if r["action"] == "propose_verdict"]
    assert (rec["check"], rec["verdict"], rec["agreement"]) == ("auth_covers_voting", "PASS", "both")
    assert run["stats"]["citations_valid"] == run["stats"]["citations"] == 2
    roles = {(e["role"], e["kind"]) for e in run["trace"]}
    assert roles >= {("investigator", "tool"), ("investigator", "agent"), ("verifier", "tool"), ("verifier", "agent")}
    assert "thread_id" not in run                                            # never handed out
    with store.tenant(1) as s:
        assert not s.get(ClaimPermission, {"claim_id": claim, "action": "vote", "tenant_id": 1}).granted
    done = flows(model).resume(1, run["run"], [{"proposal": rec["proposal_id"], "approve": True,
                                                "digest": rec["digest"]}], by="reviewer@firm")
    assert done["status"] == "applied" and done["outcomes"][0]["by"] == "reviewer@firm"
    with store.tenant(1) as s:
        assert s.get(ClaimPermission, {"claim_id": claim, "action": "vote", "tenant_id": 1}).granted
        review = s.scalar(select(CheckReview).where(CheckReview.proposal_id == rec["proposal_id"]))
        assert review.reviewer.startswith("reviewer@firm (proposed by agent investigation-")


def test_a_restart_during_review_does_not_apply_anything_twice(env, flows):
    world, store, engine = env
    claim, doc = _conditional_authority(env)
    run = flows(Agents(*_pair(doc))).start(1, claim, "lead@firm")
    rec = next(r for r in run["recommendations"] if r.get("proposal_id"))
    decision = [{"proposal": rec["proposal_id"], "approve": True, "digest": rec["digest"]}]
    later = flows(Agents([]))                                    # a new process, the same checkpoints
    assert later.resume(1, run["run"], decision, by="reviewer@firm")["status"] == "applied"
    with pytest.raises(ValueError):
        later.resume(1, run["run"], decision, by="reviewer@firm")       # the run is no longer waiting
    with store.tenant(1) as s:
        assert s.scalar(select(func.count()).select_from(CheckReview).where(
            CheckReview.proposal_id == rec["proposal_id"])) == 1


def test_a_crash_during_agent_work_resumes_at_the_step_it_was_on(env, flows):
    world, store, engine = env
    claim, doc = _conditional_authority(env)
    dying = Agents(*_pair(doc), crash_on=("verifier", 0))
    with pytest.raises(Crash):
        flows(dying).start(1, claim, "lead@firm")
    with store.tenant(1) as s:
        assert s.scalar(select(func.count()).select_from(Proposal)) == 0       # nothing proposed yet
        run = s.scalars(select(InvestigationRun)).one()
        assert run.status == "verifying" and run.packet is not None
    healthy = Agents(*_pair(doc))
    done = flows(healthy).recover(1, run.id)
    assert done["status"] == "awaiting_approval"
    assert healthy.calls == {"investigator": 0, "verifier": 2}   # the investigation was not done again
    assert len([e for e in done["trace"] if e["role"] == "investigator" and e["kind"] == "agent"]) == 1


def test_the_verifier_disputing_a_finding_sends_it_to_a_person(env, flows):
    world, store, engine = env
    claim, doc = _conditional_authority(env)
    run = flows(Agents(*_pair(doc, assessment="disputed"))).start(1, claim, "lead@firm")
    assert run["status"] == "needs_person"
    (rec,) = run["recommendations"]
    assert rec["action"] == "ask_person" and rec["agreement"] == "disputed" and rec["proposal_id"] is None
    with store.tenant(1) as s:
        assert s.scalar(select(func.count()).select_from(Proposal)) == 0


def test_one_agent_alone_proposes_on_its_own_finding(env, flows):
    world, store, engine = env
    claim, doc = _conditional_authority(env)
    investigator, _ = _pair(doc)
    model = Agents(investigator)
    run = flows(model).start(1, claim, "lead@firm", mode="single")
    assert run["status"] == "awaiting_approval" and model.calls["verifier"] == 0
    assert next(r for r in run["recommendations"] if r.get("proposal_id"))["agreement"] == "investigator_only"


def test_an_agent_cannot_overturn_a_rule(env, flows):
    """The rules keep the last word on what they decided: an agent disagreeing with a conclusive verdict is a question
    for a person, never a proposal."""
    world, store, engine = env
    claim, doc = _conditional_authority(env)
    dispute = {"requirement": "authority", "check": "auth_current", "stance": "contradicts",
               "statement": "the authority has lapsed", "citations": [{"document_id": doc, "page": 1,
                                                                        "quote": "Valid until"}]}
    run = flows(Agents([finish({"findings": [dispute]})], [finish({"reviews": [
        {"finding": 0, "assessment": "supported", "reason": "so it says"}]})])).start(1, claim, "lead@firm")
    assert run["status"] == "needs_person"
    (rec,) = run["recommendations"]
    assert rec["action"] == "ask_person" and rec["agreement"] == "rules" and "disputes the rule" in rec["reason"]


def test_a_citation_that_is_not_on_its_page_is_dropped_and_so_is_its_finding(env, flows):
    world, store, engine = env
    claim, doc = _conditional_authority(env)
    forged = {**_grant_finding(doc), "citations": [{"document_id": doc, "page": 1,
                                                    "quote": "The board approved every vote unconditionally."}]}
    other = {**_grant_finding(doc), "citations": [{"document_id": 999_999, "page": 1, "quote": APPROVAL[:60]}]}
    run = flows(Agents([finish({"findings": [forged, other]})])).start(1, claim, "lead@firm", mode="single")
    assert run["status"] == "no_change" and run["recommendations"] == []          # nothing that stands
    assert run["stats"]["citations_valid"] == 0 and len(run["stats"]["dropped"]) == 2
    with env[1].tenant(1) as s:
        assert s.scalar(select(func.count()).select_from(Proposal)) == 0


def test_a_problem_reported_without_a_verifiable_citation_goes_to_a_person(env, flows):
    world, store, engine = env
    claim, doc = _conditional_authority(env)
    uncited = {**_grant_finding(doc, stance="contradicts"), "statement": "the vote is withheld",
               "citations": [{"document_id": doc, "page": 1, "quote": "The representative may not vote at all."}]}
    run = flows(Agents([finish({"findings": [uncited]})])).start(1, claim, "lead@firm", mode="single")
    (rec,) = run["recommendations"]
    assert run["status"] == "needs_person" and rec["action"] == "ask_person" and not rec["citations"]


def test_tools_refuse_what_the_agent_may_not_see_or_hold(env, flows):
    world, store, engine = env
    claim, doc = _conditional_authority(env)
    with store.tenant(1) as s:
        outside = next(d.id for d in s.scalars(select(Document)) if d.id != doc and d.creditor_id is not None and
                       d.creditor_id != s.get(Claim, claim).creditor_id and d.doc_type == "authority_letter")
    with store.tenant(2) as s:
        theirs = s.scalars(select(Document.id)).first()
    run = flows(Agents([call("read_page", document_id=outside, page=1), call("read_page", document_id=theirs, page=1),
                        call("provenance", document_id=doc), call("search", document_id="1; drop", query="x"),
                        finish({"findings": []})])).start(1, claim, "lead@firm", mode="single")
    denied = [e for e in run["trace"] if e["kind"] == "tool" and e["outcome"] in ("denied", "error")]
    assert [e["outcome"] for e in denied] == ["denied", "denied", "denied", "error"]
    assert "not this claim's evidence" in denied[0]["reason"] and "not this claim's evidence" in denied[1]["reason"]
    assert "does not hold the tool" in denied[2]["reason"]


def test_a_quarantined_document_is_withheld_from_the_agents(env, flows):
    world, store, engine = env
    claim, doc = _conditional_authority(env)
    with store.tenant(1) as s:
        s.get(Document, doc).hostile = True
    run = flows(Agents([call("read_page", document_id=doc, page=1), finish({"findings": []})])).start(
        1, claim, "lead@firm", mode="single")
    with store.tenant(1) as s:
        event = s.scalars(select(AgentEvent).where(AgentEvent.tool == "read_page")).one()
    assert event.outcome == "ok" and run["status"] in ("no_change", "needs_person")


def test_an_agent_that_never_finishes_stops_at_its_budget(env, flows):
    world, store, engine = env
    claim, _ = _conditional_authority(env)
    budgets = Budgets(investigator=Budget(steps=4), tool_calls=3)
    run = flows(Agents([call("claim_summary")]), budgets).start(1, claim, "lead@firm", mode="single")
    summary = next(e for e in run["trace"] if e["kind"] == "agent")
    assert summary["outcome"] == "step budget" and run["status"] == "needs_person"
    tools = [e for e in run["trace"] if e["kind"] == "tool"]
    assert len(tools) == 4 and [e["outcome"] for e in tools].count("denied") == 1     # the fourth call: no calls left


def test_evidence_that_changes_during_review_makes_the_approval_stale(env, flows):
    world, store, engine = env
    claim, doc = _conditional_authority(env)
    run = flows(Agents(*_pair(doc))).start(1, claim, "lead@firm")
    rec = next(r for r in run["recommendations"] if r.get("proposal_id"))
    # a correction a check reads: the evidence the proposal rests on is not what it was
    engine.correct_field(1, doc, "issue_date", (world.as_of - timedelta(days=3)).strftime("%d.%m.%Y"),
                         "reviewer@firm")
    done = flows(Agents([])).resume(1, run["run"], [{"proposal": rec["proposal_id"], "approve": True,
                                                     "digest": rec["digest"]}], by="reviewer@firm")
    assert done["status"] == "stale" and done["outcomes"][0]["outcome"] == "stale"
    with store.tenant(1) as s:
        assert s.scalar(select(func.count()).select_from(CheckReview).where(
            CheckReview.proposal_id == rec["proposal_id"])) == 0


def test_a_run_belongs_to_its_tenant(env, flows):
    world, store, engine = env
    claim, doc = _conditional_authority(env)
    flow = flows(Agents(*_pair(doc)))
    run = flow.start(1, claim, "lead@firm")
    with pytest.raises(KeyError):
        flow.status(2, run["run"])
    with pytest.raises(KeyError):
        flow.resume(2, run["run"], [], by="someone@other")
    with pytest.raises(ValueError):                      # and a decision on a proposal that is not the run's
        flow.resume(1, run["run"], [{"proposal": 10_000, "approve": True}], by="reviewer@firm")


def test_cancelling_a_run_that_waits_for_approval_declines_its_proposals(env, flows):
    world, store, engine = env
    claim, doc = _conditional_authority(env)
    flow = flows(Agents(*_pair(doc)))
    run = flow.start(1, claim, "lead@firm")
    assert flow.cancel(1, run["run"], "lead@firm")["status"] == "cancelled"
    with store.tenant(1) as s:
        assert {p.status for p in s.scalars(select(Proposal))} == {"declined"}

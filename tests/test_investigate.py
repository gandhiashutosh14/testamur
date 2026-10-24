"""The investigator-verifier workflow, driven by a scripted model so every path is exact: two agents in two contexts,
a coordinator that validates their answers and alone proposes, a person's approval applied once, checkpoints that
survive a crash during agent work and a restart during review, budgets, scope and stale evidence."""
from datetime import timedelta

import pytest
from scripted import APPROVAL, Agents, Crash, _conditional_authority, _grant_finding, _pair, call, finish
from sqlalchemy import func, select

from testamur.investigate import Budgets, Investigations, sqlite_checkpointer
from testamur.investigate.agent import Budget
from testamur.store import (
    AgentEvent,
    CheckReview,
    Claim,
    ClaimPermission,
    Document,
    InvestigationRun,
    Proposal,
)


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
    seen = []

    def look(messages):                     # what the agent was shown, after its task
        seen.append(" ".join(m["content"] for m in messages[1:]))
        return finish({"findings": []})
    run = flows(Agents([call("read_page", document_id=doc, page=1), call("search", document_id=doc, query="board"),
                        look])).start(1, claim, "lead@firm", mode="single")
    with store.tenant(1) as s:
        events = s.scalars(select(AgentEvent).where(AgentEvent.kind == "tool")).all()
    assert [e.outcome for e in events] == ["ok", "ok"] and run["status"] in ("no_change", "needs_person")
    assert seen and APPROVAL[:40] not in seen[0] and seen[0].count('"withheld":') == 2


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

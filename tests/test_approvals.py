"""A person's confirmation of an agent's proposal applies exactly what the person was shown, once, atomically. A
proposal made on one document is never applied to its replacement; one whose evidence changed is refused as stale; a
crash at any step leaves either nothing or everything; two people confirming at once apply it once."""
import threading
from datetime import datetime, time

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from testamur import agents
from testamur.actionsim import renewed_authority
from testamur.model import Status
from testamur.store import AuditRow, CheckReview, Claim, Creditor, OutboxEvent, Proposal, RequirementState


def _authority(store, tenant=1):
    """A claim whose own authority is satisfied: its claim id, creditor, matter, name and current document."""
    with store.tenant(tenant) as s:
        for st in s.scalars(select(RequirementState).where(RequirementState.requirement_id == "authority",
                                                           RequirementState.status == Status.SATISFIED,
                                                           RequirementState.via_trustee.is_(False))
                            .order_by(RequirementState.claim_id)):
            e = s.get(Claim, st.claim_id)
            return e.id, e.creditor_id, e.matter_id, s.get(Creditor, e.creditor_id).legal_name, st.document_id
    raise AssertionError("the tiny world needs a satisfied authority")


def _propose(engine, claim, check="auth_creditor_matches", verdict="FAIL"):
    return agents.propose(engine, 1, "agent-a", "check_verdict",
                          {"claim": claim, "requirement": "authority", "check": check, "verdict": verdict},
                          "the signatory is not the creditor's")


def _effects(store, proposal):
    with store.tenant(1) as s:
        return s.scalar(select(func.count()).select_from(CheckReview).where(CheckReview.proposal_id == proposal))


def _status_of(store, proposal):
    with store.tenant(1) as s:
        return s.get(Proposal, proposal).status


def test_a_proposal_made_on_one_document_is_never_applied_to_its_replacement(env):
    """The review's case: proposed against document A; a renewed authority, document B, becomes current; the
    confirmation used to write the person's verdict onto B. Now it is refused as stale and nothing is written."""
    world, store, engine = env
    claim, creditor, matter, name, doc_a = _authority(store)
    p = _propose(engine, claim)
    now = datetime.combine(world.as_of, time(9))
    engine.process(1, engine.ingest(1, matter, "renewal.pdf", renewed_authority(name, world.as_of, seed=5),
                                    "creditor_portal", now, declared_creditor_id=creditor))
    with store.tenant(1) as s:
        doc_b = s.get(RequirementState, {"claim_id": claim, "requirement_id": "authority", "tenant_id": 1}).document_id
    assert doc_b != doc_a
    assert agents.decide(engine, 1, p["proposal"], "reviewer@firm", confirm=True) == "stale"
    assert _effects(store, p["proposal"]) == 0 and _status_of(store, p["proposal"]) == "stale"
    with store.tenant(1) as s:
        assert s.scalar(select(func.count()).select_from(CheckReview).where(CheckReview.claim_id == claim)) == 0
        stale = s.scalar(select(AuditRow).where(AuditRow.action == "proposal_stale"))
        assert stale.detail["bound"]["document"] == doc_a and stale.detail["now"]["document"] == doc_b


def test_a_proposal_is_stale_when_the_evidence_changes_but_the_status_does_not(env):
    """A correction that leaves the status where it was still changes what the proposal rests on."""
    world, store, engine = env
    claim, _, _, _, doc = _authority(store)
    with store.tenant(1) as s:
        before = s.get(RequirementState, {"claim_id": claim, "requirement_id": "authority", "tenant_id": 1})
        status, digest = before.status, before.evidence_digest
    p = _propose(engine, claim)
    engine.correct_field(1, doc, "representative", "Meera Iyer, Authorised Signatory", "reviewer@firm")
    with store.tenant(1) as s:
        after = s.get(RequirementState, {"claim_id": claim, "requirement_id": "authority", "tenant_id": 1})
        assert after.status == status                         # the headline did not move ...
    engine.correct_field(1, doc, "powers", "To attend, speak and vote for the creditor at every meeting of creditors",
                         "reviewer@firm")
    with store.tenant(1) as s:
        after = s.get(RequirementState, {"claim_id": claim, "requirement_id": "authority", "tenant_id": 1})
        assert after.status == status and after.evidence_digest != digest      # ... the evidence did
    assert agents.decide(engine, 1, p["proposal"], "reviewer@firm", confirm=True) == "stale"
    assert _effects(store, p["proposal"]) == 0


def test_the_person_confirms_what_they_were_shown(env):
    world, store, engine = env
    claim, *_ = _authority(store)
    p = _propose(engine, claim)
    with pytest.raises(agents.StaleProposal):
        agents.decide(engine, 1, p["proposal"], "reviewer@firm", confirm=True, seen="0" * 16)
    assert _status_of(store, p["proposal"]) == "proposed" and _effects(store, p["proposal"]) == 0
    agents.decide(engine, 1, p["proposal"], "reviewer@firm", confirm=True, seen=p["digest"])
    assert _status_of(store, p["proposal"]) == "applied" and _effects(store, p["proposal"]) == 1


@pytest.mark.parametrize("step", ["after_effect", "before_commit"])
def test_a_crash_before_the_commit_leaves_nothing(env, step):
    world, store, engine = env
    claim, *_ = _authority(store)
    p = _propose(engine, claim)
    with pytest.raises(RuntimeError):
        agents.decide(engine, 1, p["proposal"], "reviewer@firm", confirm=True, fail_at=step)
    assert _status_of(store, p["proposal"]) == "proposed" and _effects(store, p["proposal"]) == 0
    with store.tenant(1) as s:
        assert s.scalar(select(OutboxEvent).where(OutboxEvent.key == f"proposal:{p['proposal']}")) is None
        st = s.get(RequirementState, {"claim_id": claim, "requirement_id": "authority", "tenant_id": 1})
        assert st.status == Status.SATISFIED                  # the engine's change rolled back with the rest
    agents.decide(engine, 1, p["proposal"], "reviewer@firm", confirm=True)         # and it can still be confirmed
    assert _effects(store, p["proposal"]) == 1


def test_a_crash_after_the_commit_leaves_everything_once(env):
    world, store, engine = env
    claim, *_ = _authority(store)
    p = _propose(engine, claim)
    with pytest.raises(RuntimeError):
        agents.decide(engine, 1, p["proposal"], "reviewer@firm", confirm=True, fail_at="after_commit")
    assert _status_of(store, p["proposal"]) == "applied" and _effects(store, p["proposal"]) == 1
    with pytest.raises(ValueError):                                                # a retry is refused
        agents.decide(engine, 1, p["proposal"], "reviewer@firm", confirm=True)
    with store.tenant(1) as s:
        st = s.get(RequirementState, {"claim_id": claim, "requirement_id": "authority", "tenant_id": 1})
        assert st.status == Status.NOT_SATISFIED
        assert s.scalar(select(func.count()).select_from(OutboxEvent).where(
            OutboxEvent.key == f"proposal:{p['proposal']}")) == 1


def test_two_people_confirming_at_once_apply_it_once(env):
    world, store, engine = env
    claim, *_ = _authority(store)
    p = _propose(engine, claim)
    outcomes, errors, start = [], [], threading.Barrier(2)

    def confirm(who):
        start.wait()
        try:
            outcomes.append(agents.decide(engine, 1, p["proposal"], who, confirm=True))
        except ValueError as exc:
            errors.append(exc)
    threads = [threading.Thread(target=confirm, args=(f"reviewer-{i}@firm",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(outcomes) == 1 and len(errors) == 1 and "already applied" in str(errors[0])
    assert _effects(store, p["proposal"]) == 1


def test_a_proposals_effect_cannot_exist_twice(env):
    """The effect carries the proposal's id under a unique key: even a path that bypassed the proposal's own state
    could not apply it twice."""
    world, store, engine = env
    claim, _, _, _, doc = _authority(store)
    p = _propose(engine, claim)
    agents.decide(engine, 1, p["proposal"], "reviewer@firm", confirm=True)
    with pytest.raises(IntegrityError), store.tenant(1) as s:
        s.add(CheckReview(claim_id=claim, requirement_id="authority", check_id="auth_creditor_matches",
                          document_id=doc, verdict="PASS", machine_verdict="PASS", rule_digest="x",
                          reviewer="someone", proposal_id=p["proposal"]))

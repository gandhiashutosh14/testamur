"""People's decisions are inputs: they change statuses, survive re-runs, apply only under the rule they were made
under, and never open a door the engine keeps shut (the model still never reads a hostile document)."""
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from testamur.engine import Engine
from testamur.model import Status, Verdict
from testamur.pack import load_pack
from testamur.store import CheckReview, Document, FieldCorrection, RequirementState, VerdictRow


def _status(store, tenant, eid, rid):
    with store.tenant(tenant) as s:
        return Status(s.get(RequirementState, {"engagement_id": eid, "requirement_id": rid, "tenant_id": tenant}).status)


def _first_state(store, tenant, rid, status):
    with store.tenant(tenant) as s:
        st = s.scalars(select(RequirementState).where(RequirementState.requirement_id == rid,
                                                      RequirementState.status == status)
                       .order_by(RequirementState.engagement_id)).first()
        assert st is not None, f"the tiny world has no {rid} in {status}"
        return st.engagement_id, st.document_id


def _rerun(world, engine):
    for t in world.tenants:
        engine.reindex(t.id)
        engine.refresh(t.id)


def test_a_persons_verdict_stands_through_a_reindex_and_records_the_engine_was_wrong(env):
    world, store, engine = env
    eid, doc_id = _first_state(store, 1, "insurance", Status.SATISFIED)
    after = engine.review_check(1, eid, "insurance", "ins_limit_meets_tier", "FAIL", "reviewer@buyer",
                                note="the limit is per claim, not per occurrence")
    assert after == Status.NOT_SATISFIED
    _rerun(world, engine)
    assert _status(store, 1, eid, "insurance") == Status.NOT_SATISFIED
    with store.tenant(1) as s:
        review = s.scalars(select(CheckReview)).one()
        assert (review.machine_verdict, review.verdict, review.document_id) == ("PASS", "FAIL", doc_id)
        machine = s.scalar(select(VerdictRow).where(VerdictRow.engagement_id == eid,
                                                    VerdictRow.check_id == "ins_limit_meets_tier"))
        assert machine.verdict == "PASS"              # the engine's own verdict is kept beside the person's


def test_a_decision_made_under_an_older_rule_no_longer_applies(env):
    world, store, engine = env
    eid, _ = _first_state(store, 1, "insurance", Status.SATISFIED)
    engine.review_check(1, eid, "insurance", "ins_limit_meets_tier", "FAIL", "reviewer@buyer")
    changed = load_pack().model_copy(deep=True)
    changed.tier_minimum_liability = {k: v + 1 for k, v in changed.tier_minimum_liability.items()}
    later = Engine(store, changed, world.as_of, engine.blob_root)
    assert changed.rule_digest("ins_limit_meets_tier") != load_pack().rule_digest("ins_limit_meets_tier")
    assert changed.rule_digest("ins_cover_current") == load_pack().rule_digest("ins_cover_current")
    later.refresh(1)
    assert _status(store, 1, eid, "insurance") == Status.SATISFIED


def test_overturning_a_deciding_failure_lets_the_skipped_model_checks_run(env):
    world, store, engine = env
    unsigned = next(u for u in world.uploads if u.truth.variant == "unsigned")
    tenant = unsigned.tenant_id
    with store.tenant(tenant) as s:
        failed = s.scalars(select(VerdictRow).where(VerdictRow.check_id == "msa_signed",
                                                    VerdictRow.verdict == "FAIL")).first()
        eid, doc_id = failed.engagement_id, failed.document_id
        skipped = {v.check_id for v in s.scalars(select(VerdictRow).where(
            VerdictRow.engagement_id == eid, VerdictRow.document_id == doc_id, VerdictRow.tier == "model",
            VerdictRow.verdict == "SKIPPED", VerdictRow.reason.like("not evaluated%")))}
    assert skipped, "an unsigned agreement should have skipped its clause checks"
    assert _status(store, tenant, eid, "master_agreement") == Status.NOT_SATISFIED
    engine.review_check(tenant, eid, "master_agreement", "msa_signed", "PASS", "reviewer@buyer",
                        note="signed copy confirmed with the supplier")
    with store.tenant(tenant) as s:
        now = {v.check_id: v.verdict for v in s.scalars(select(VerdictRow).where(
            VerdictRow.engagement_id == eid, VerdictRow.document_id == doc_id, VerdictRow.check_id.in_(skipped)))}
    assert now and all(v == Verdict.ABSTAIN for v in now.values())     # no model configured: a person's turn


def test_a_corrected_reading_is_judged_by_the_rules_and_survives_a_reindex(env):
    world, store, engine = env
    eid, doc_id = _first_state(store, 1, "insurance", Status.SATISFIED)
    with pytest.raises(ValueError):
        engine.correct_field(1, doc_id, "gl_limit", "five million", "reviewer@buyer")
    engine.correct_field(1, doc_id, "gl_limit", "500,000 EUR", "reviewer@buyer", note="page 2 schedule")
    assert _status(store, 1, eid, "insurance") == Status.NOT_SATISFIED
    _rerun(world, engine)
    assert _status(store, 1, eid, "insurance") == Status.NOT_SATISFIED
    with store.tenant(1) as s:
        fix = s.scalars(select(FieldCorrection)).one()
        assert fix.machine_raw and fix.machine_raw != fix.raw
        row = s.scalar(select(VerdictRow).where(VerdictRow.engagement_id == eid,
                                                VerdictRow.check_id == "ins_limit_meets_tier"))
        assert row.verdict == "FAIL" and row.evidence[0]["source"] == "reviewer"


def test_assigning_a_document_settles_it_and_a_reindex_keeps_it(env):
    world, store, engine = env
    misfiled = next(u for u in world.uploads if u.truth.variant == "normal+misfiled" and u.truth.expect == "abstain")
    with store.tenant(misfiled.tenant_id) as s:
        doc = s.scalars(select(Document).where(Document.state == "needs_review",
                                               Document.doc_type == misfiled.truth.doc_type)).first()
        doc_id = doc.id
    engine.assign(misfiled.tenant_id, doc_id, misfiled.truth.owner_supplier_id, "reviewer@buyer")
    _rerun(world, engine)
    with store.tenant(misfiled.tenant_id) as s:
        doc = s.get(Document, doc_id)
        assert (doc.state, doc.supplier_id, doc.match_method) == ("matched", misfiled.truth.owner_supplier_id,
                                                                   "reviewer")
    assert all(i.document_id != doc_id or i.kind == "check" for i in engine.queue(misfiled.tenant_id))


def test_a_rejected_document_stops_holding_requirements_open(env):
    world, store, engine = env
    with store.tenant(1) as s:
        pending = s.scalars(select(Document).where(Document.state == "needs_review")).all()
        assert pending
        held = [st for st in s.scalars(select(RequirementState)).all()
                if any("unresolved document" in r for r in st.reasons or [])]
    for d in pending:
        engine.reject(1, d.id, "reviewer@buyer", note="not evidence for any supplier of ours")
    with store.tenant(1) as s:
        assert {d.state for d in s.scalars(select(Document).where(Document.id.in_([d.id for d in pending])))} == \
            {"rejected"}
        for st in held:
            now = s.get(RequirementState, {"engagement_id": st.engagement_id, "requirement_id": st.requirement_id,
                                           "tenant_id": 1})
            assert not any("unresolved document" in r for r in now.reasons or [])
    assert not [i for i in engine.queue(1) if i.kind == "document_owner"]


def test_a_cleared_document_may_vouch_but_the_model_still_never_reads_it(env):
    world, store, engine = env
    hostile = next(u for u in world.uploads if u.truth.injection and u.tenant_id == 1)
    with store.tenant(1) as s:
        doc = s.scalars(select(Document).where(Document.hostile.is_(True))).first()
        doc_id = doc.id
        held = [st for st in s.scalars(select(RequirementState).where(RequirementState.document_id == doc_id))]
    assert held and all(st.status == Status.NEEDS_REVIEW for st in held), hostile.file
    engine.clear(1, doc_id, "security@buyer", note="read in full; the instruction is ignored")
    assert any(_status(store, 1, st.engagement_id, st.requirement_id) != Status.NEEDS_REVIEW for st in held)

    class Tripwire:
        """Records every prompt; the engine swallows provider errors, so raising would prove nothing."""
        model_id = "tripwire"

        def __init__(self):
            self.prompts = []

        def chat(self, messages, max_tokens=200):
            self.prompts.append(messages)
            raise RuntimeError("a hostile document reached the model")
    wire = Tripwire()
    guarded = Engine(store, load_pack(), world.as_of, engine.blob_root, model=wire)
    with store.tenant(1) as s:
        doc = s.get(Document, doc_id)
        assert doc.cleared
        ex = guarded._extraction(s, 1, doc.sha256)
        r = guarded._model_check(s, 1, "clause_liability_cap", "master_agreement", doc, ex, {})
    assert r.verdict == Verdict.ABSTAIN and not r.transient and wire.prompts == []


def test_people_decide_pass_or_fail_and_only_inside_their_tenant(env):
    world, store, engine = env
    eid, _ = _first_state(store, 1, "insurance", Status.SATISFIED)
    with pytest.raises(ValueError):
        engine.review_check(1, eid, "insurance", "ins_cover_current", "ABSTAIN", "reviewer@buyer")
    with store.tenant(2) as s:
        other = s.scalars(select(Document)).first().id
    with pytest.raises(KeyError):
        engine.reject(1, other, "reviewer@buyer")


def test_the_queue_lists_what_needs_a_person(env):
    world, store, engine = env
    kinds = {i.kind for t in world.tenants for i in engine.queue(t.id)}
    assert {"document_owner", "document_type", "document_security", "check"} <= kinds
    checks = [i for i in engine.queue(1) if i.kind == "check"]
    assert checks and not any(i.retrying for i in checks if "no model is configured" in i.reason)


def test_cover_that_lapses_overnight_is_expired_the_next_day_without_a_new_upload(env):
    world, store, engine = env
    eid, doc_id = _first_state(store, 1, "insurance", Status.SATISFIED)
    with store.tenant(1) as s:
        expiry = date.fromisoformat(s.get(Document, doc_id).fields["expiry_date"]["value"])
    Engine(store, load_pack(), expiry, engine.blob_root).refresh(1)
    assert _status(store, 1, eid, "insurance") == Status.SATISFIED_WITH_FINDINGS   # covered, but renewal is due
    Engine(store, load_pack(), expiry + timedelta(days=1), engine.blob_root).refresh(1)
    assert _status(store, 1, eid, "insurance") == Status.EXPIRED


def test_a_persons_pass_on_a_dated_check_holds_that_day_and_a_fail_holds_after(env):
    world, store, engine = env
    found = None
    for t in world.tenants:
        with store.tenant(t.id) as s:
            row = s.scalars(select(VerdictRow).where(VerdictRow.check_id == "ins_renewal_due",
                                                     VerdictRow.verdict == "FAIL")).first()
            if row is not None:
                found = (t.id, row.engagement_id)
                break
    assert found, "the tiny world has a certificate due for renewal"
    tenant, eid = found
    engine.review_check(tenant, eid, "insurance", "ins_renewal_due", "PASS", "reviewer@buyer",
                        note="renewal confirmed by the broker by phone")
    assert _status(store, tenant, eid, "insurance") == Status.SATISFIED
    tomorrow = Engine(store, load_pack(), engine.as_of + timedelta(days=1), engine.blob_root)
    tomorrow.refresh(tenant)
    assert _status(store, tenant, eid, "insurance") == Status.SATISFIED_WITH_FINDINGS   # the PASS was for that day
    tomorrow.review_check(tenant, eid, "insurance", "ins_cover_current", "FAIL", "reviewer@buyer",
                          note="the insurer says the policy was cancelled")
    Engine(store, load_pack(), engine.as_of + timedelta(days=2), engine.blob_root).refresh(tenant)
    assert _status(store, tenant, eid, "insurance") == Status.NOT_SATISFIED            # a FAIL stays

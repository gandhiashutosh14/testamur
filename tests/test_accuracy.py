"""Holding accuracy: exact bounds, audit samples that do not depend on what a verdict says, and a check that broke
its budget stops vouching until a person re-certifies it."""
import pytest
from sqlalchemy import select

from testamur.accuracy import audits_needed, binom_cdf, cp_lower, cp_upper
from testamur.engine import Engine
from testamur.model import Status
from testamur.pack import load_pack
from testamur.store import CheckReview, RequirementState


@pytest.mark.parametrize("k,n", [(0, 1), (0, 300), (1, 100), (5, 100), (3, 40), (40, 40)])
def test_clopper_pearson_bounds_are_exact(k, n):
    alpha = 0.05
    upper = cp_upper(k, n, alpha)
    if k < n:
        assert binom_cdf(k, n, upper) == pytest.approx(alpha, abs=1e-6)       # P(X <= k) at the upper bound
    else:
        assert upper == 1.0
    lower = cp_lower(k, n, alpha)
    if k > 0:
        assert 1 - binom_cdf(k - 1, n, lower) == pytest.approx(alpha, abs=1e-6)   # P(X >= k) at the lower bound
    else:
        assert lower == 0.0
    assert lower <= k / n <= upper
    assert cp_upper(0, 300) == pytest.approx(1 - 0.05 ** (1 / 300))
    assert audits_needed(0.01) == 299


def _authority_passes(store, tenant=1):
    with store.tenant(tenant) as s:
        return [(st.claim_id, st.document_id) for st in s.scalars(select(RequirementState).where(
            RequirementState.requirement_id == "authority", RequirementState.status == Status.SATISFIED)
            .order_by(RequirementState.claim_id))]


def _status(store, eid, rid="authority", tenant=1):
    with store.tenant(tenant) as s:
        return Status(s.get(RequirementState, {"claim_id": eid, "requirement_id": rid, "tenant_id": tenant}).status)


def test_one_confirmed_false_pass_demotes_a_critical_check_until_it_is_recertified(env):
    world, store, engine = env
    passes = _authority_passes(store)
    assert len(passes) >= 2, "the tiny world needs two satisfied authority requirements"
    (audited, _), (other, _) = passes[0], passes[1]
    assert engine.health(1)["auth_creditor_matches"].state == "uncertified"
    engine.review_check(1, audited, "authority", "auth_creditor_matches", "FAIL", "auditor@firm", kind="audit",
                        note="the limit shown is the aggregate, not per occurrence")
    h = engine.health(1)["auth_creditor_matches"]
    assert (h.state, h.audited_pass, h.false_pass) == ("breached", 1, 1)
    assert _status(store, audited) == Status.NOT_SATISFIED                # the person's FAIL
    assert _status(store, other) == Status.NEEDS_REVIEW                   # its PASS no longer vouches
    confirm = [i for i in engine.queue(1) if i.confirm and i.claim_id == other]
    assert confirm and confirm[0].check_id == "auth_creditor_matches"
    engine.review_check(1, other, "authority", "auth_creditor_matches", "PASS", "reviewer@firm")
    with store.tenant(1) as s:
        assert s.scalars(select(CheckReview).where(CheckReview.claim_id == other)).one().kind == "confirm"
    assert _status(store, other) == Status.SATISFIED
    engine.recertify(1, "auth_creditor_matches", "lead@firm", note="names now compared without the group prefix")
    assert engine.health(1)["auth_creditor_matches"].state == "uncertified"
    assert all(_status(store, eid) == Status.SATISFIED for eid, _ in passes[1:])


def test_audits_under_an_older_rule_do_not_count(env):
    world, store, engine = env
    (audited, _), (other, _) = _authority_passes(store)[:2]
    engine.review_check(1, audited, "authority", "auth_creditor_matches", "FAIL", "auditor@firm", kind="audit")
    assert engine.health(1)["auth_creditor_matches"].state == "breached"
    fixed = load_pack().model_copy(deep=True)
    fixed.checks["auth_creditor_matches"].params["pass_at"] = 95
    later = Engine(store, fixed, world.as_of, engine.blob_root)
    assert later.health(1)["auth_creditor_matches"].state == "uncertified"


def test_only_random_reviews_estimate_accuracy_but_any_false_pass_is_an_incident(env):
    world, store, engine = env
    item = next(i for i in engine.queue(1) if i.kind == "check" and i.check_id.startswith("clause_"))
    engine.review_check(1, item.claim_id, item.requirement_id, item.check_id, "PASS", "legal@firm")
    h = engine.health(1)[item.check_id]
    assert (h.audited_pass, h.audited_fail, h.incidents, h.state) == (0, 0, 0, "uncertified")
    (complained, _), (other, _) = _authority_passes(store)[:2]
    engine.review_check(1, complained, "authority", "auth_creditor_matches", "FAIL", "reviewer@firm",
                        note="a claim was refused: the limit is per claim")    # found by a complaint, not a sample
    h = engine.health(1)["auth_creditor_matches"]
    assert (h.audited_pass, h.false_pass, h.incidents, h.state) == (0, 0, 1, "breached")
    assert _status(store, other) == Status.NEEDS_REVIEW


def test_the_audit_sample_is_reproducible_and_draws_only_conclusive_current_verdicts(env):
    world, store, engine = env
    first, again = engine.audit_sample(1, seed="day-1"), engine.audit_sample(1, seed="day-1")
    assert [(i.claim_id, i.check_id) for i in first] == [(i.claim_id, i.check_id) for i in again]
    everything = load_pack().model_copy(deep=True)
    everything.audit.audit_rate = {k: 1.0 for k in everything.audit.audit_rate}
    census = Engine(store, everything, world.as_of, engine.blob_root).audit_sample(1)
    assert len(census) > len(first) > 0
    assert all(i.reason.startswith(("the engine said PASS", "the engine said FAIL")) for i in census)
    nothing = load_pack().model_copy(deep=True)
    nothing.audit.audit_rate = {k: 0.0 for k in nothing.audit.audit_rate}
    assert Engine(store, nothing, world.as_of, engine.blob_root).audit_sample(1) == []
    item = first[0]
    engine.review_check(1, item.claim_id, item.requirement_id, item.check_id,
                        "PASS" if "said PASS" in item.reason else "FAIL", "auditor@firm", kind="audit")
    assert (item.claim_id, item.check_id) not in {(i.claim_id, i.check_id)
                                                       for i in engine.audit_sample(1, seed="day-1")}

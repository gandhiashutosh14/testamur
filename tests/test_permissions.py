"""Whether a document suffices and what the evidence permits are separate decisions. An authority that lets the
representative file but not vote is good enough as a document (a major finding), and still no vote is counted."""
from datetime import datetime, time

from sqlalchemy import select

from testamur.actions import dispatch
from testamur.model import Status
from testamur.pack import load_pack
from testamur.store import ClaimPermission, Hold, RequirementState


def _satisfied_voter(store, tenant=1):
    """A claim whose evidence permits it to vote, with its authority's document."""
    with store.tenant(tenant) as s:
        for p in s.scalars(select(ClaimPermission).where(ClaimPermission.action == "vote",
                                                         ClaimPermission.granted.is_(True))):
            st = s.get(RequirementState, {"claim_id": p.claim_id, "requirement_id": "authority", "tenant_id": tenant})
            if st.status == Status.SATISFIED and not st.via_trustee:
                return p.claim_id, st.document_id
    raise AssertionError("the tiny world needs a claim that may vote on its own authority")


def _permission(store, claim, action, tenant=1):
    with store.tenant(tenant) as s:
        return s.get(ClaimPermission, {"claim_id": claim, "action": action, "tenant_id": tenant})


def test_a_major_finding_on_the_authority_withholds_the_vote_but_not_the_document(env):
    world, store, engine = env
    claim, doc = _satisfied_voter(store)
    payout_before = _permission(store, claim, "payout").granted
    engine.correct_field(1, doc, "powers", "To sign and submit the proof of claim and to receive notices",
                         "reviewer@firm", note="page 2 of the letter")
    with store.tenant(1) as s:
        st = s.get(RequirementState, {"claim_id": claim, "requirement_id": "authority", "tenant_id": 1})
        assert st.status == Status.SATISFIED_WITH_FINDINGS and "auth_covers_voting" in st.failing   # good to file
    vote = _permission(store, claim, "vote")
    assert not vote.granted and vote.unmet == ["auth_covers_voting is FAIL"]                      # but not to vote
    assert _permission(store, claim, "payout").granted == payout_before                           # payout untouched
    now = datetime.combine(world.as_of, time(9))
    dispatch(store, load_pack(), 1, now)
    with store.tenant(1) as s:
        holds = {h.action: h for h in s.scalars(select(Hold).where(Hold.claim_id == claim, Hold.released_at.is_(None)))}
        assert "vote" in holds and holds["vote"].reasons == ["auth_covers_voting is FAIL"]
        assert ("payout" in holds) == (not payout_before)


def test_a_vote_granted_only_on_condition_waits_for_a_person(env):
    world, store, engine = env
    claim, doc = _satisfied_voter(store)
    engine.correct_field(1, doc, "powers", "To vote at meetings, subject to the prior approval of the board",
                         "reviewer@firm")
    vote = _permission(store, claim, "vote")
    assert not vote.granted and vote.unmet == ["auth_covers_voting is ABSTAIN"]
    engine.review_check(1, claim, "authority", "auth_covers_voting", "PASS", "legal@firm",
                        note="board approval of 14 August on file")
    assert _permission(store, claim, "vote").granted                     # a person's decision is an input like any


def test_every_claim_has_each_permission_decided(env):
    world, store, engine = env
    actions = set(load_pack().permissions)
    with store.system() as s:
        decided = {(p.tenant_id, p.claim_id, p.action) for p in s.scalars(select(ClaimPermission))}
    assert decided == {(e.tenant_id, e.id, a) for e in world.claims for a in actions}

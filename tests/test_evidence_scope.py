"""Evidence belongs to the process it was filed in. A proof of claim, an authority, a charge or a loan agreement filed in
one insolvency process never decides a claim in another because the creditor is the same; only what the pack shares
across processes on purpose (a bank account confirmation) does."""
from datetime import date, datetime

from sqlalchemy import select

from testamur.model import Status
from testamur.store import Attestation, Claim, Document, Matter, RequirementState


def _status(store, tenant, claim, rid):
    with store.tenant(tenant) as s:
        return Status(s.get(RequirementState, {"claim_id": claim, "requirement_id": rid, "tenant_id": tenant}).status)


def _new_process(store, tenant, creditor_id, claim_type="unsecured"):
    """A fresh process in the tenant and a claim in it for an existing creditor, with no upload at all."""
    with store.tenant(tenant) as s:
        mid = s.scalar(select(Matter.id).order_by(Matter.id.desc())) + 1000
        s.add(Matter(id=mid, name="Narmada Basin Textiles Ltd", debtor_cin="", commencement=date(2026, 6, 1),
                     announced=date(2026, 6, 3), claims_due=date(2026, 6, 15)))
        cid = s.scalar(select(Claim.id).order_by(Claim.id.desc())) + 1000
        s.add(Claim(id=cid, matter_id=mid, creditor_id=creditor_id, tier="high", claim_type=claim_type,
                    facility_type="term_loan", book_balance=10_000_000, sanctioned=10_000_000))
    return mid, cid


def _satisfied(store, tenant, rid):
    with store.tenant(tenant) as s:
        rows = s.scalars(select(RequirementState).where(RequirementState.requirement_id == rid,
                                                        RequirementState.status == Status.SATISFIED)).all()
        return [(r.claim_id, s.get(Claim, r.claim_id).creditor_id) for r in rows]


def test_a_new_process_with_no_proof_of_claim_stays_missing(env):
    """The review's case: a creditor whose proof of claim is satisfied in one process claims in a new one with no
    upload there. Before, the first process's Form C stood in and the new claim read SATISFIED."""
    world, store, engine = env
    (elsewhere, creditor), *_ = _satisfied(store, 1, "proof_of_claim")
    _, new = _new_process(store, 1, creditor)
    engine.refresh(1)
    assert _status(store, 1, elsewhere, "proof_of_claim") == Status.SATISFIED
    assert _status(store, 1, new, "proof_of_claim") == Status.MISSING
    assert _status(store, 1, new, "authority") in (Status.MISSING, Status.NEEDS_REVIEW)
    with store.tenant(1) as s:
        assert s.get(RequirementState, {"claim_id": new, "requirement_id": "proof_of_claim",
                                        "tenant_id": 1}).document_id is None


def test_only_evidence_the_pack_shares_across_processes_counts_in_the_new_one(env):
    """A bank account confirmation is the creditor's in every process (pack: evidence_scope creditor), so the new
    claim is judged on it; nothing else crosses."""
    world, store, engine = env
    (elsewhere, creditor), *_ = _satisfied(store, 1, "bank_details")
    _, new = _new_process(store, 1, creditor)
    engine.refresh(1)
    with store.tenant(1) as s:
        old = s.get(RequirementState, {"claim_id": elsewhere, "requirement_id": "bank_details", "tenant_id": 1})
        shared = s.get(RequirementState, {"claim_id": new, "requirement_id": "bank_details", "tenant_id": 1})
        assert shared.document_id == old.document_id and shared.status == Status.SATISFIED
    assert engine.pack.doc_types["bank_mandate"].evidence_scope == "creditor"
    assert {t.evidence_scope for n, t in engine.pack.doc_types.items() if n != "bank_mandate"} == {"matter"}


def test_the_same_file_filed_in_two_processes_is_judged_in_each(env):
    """The same Form C bytes filed in the creditor's other process are a second document, judged against that
    process: it names another corporate debtor, so it cannot satisfy the claim there."""
    world, store, engine = env
    (elsewhere, creditor), *_ = _satisfied(store, 1, "proof_of_claim")
    mid, new = _new_process(store, 1, creditor)
    with store.tenant(1) as s:
        doc = s.get(Document, s.get(RequirementState, {"claim_id": elsewhere, "requirement_id": "proof_of_claim",
                                                       "tenant_id": 1}).document_id)
        sha = doc.sha256
    blob = next(p for p in (engine.blob_root / "1").rglob(sha))
    upload = engine.ingest(1, mid, "form c.pdf", blob.read_bytes(), "staff_upload", datetime(2026, 6, 10, 10, 0))
    out = engine.process(1, upload)
    assert out.document_id != doc.id and not out.deduplicated            # a second document, not the first one
    st, results = engine.verdicts(1, new, "proof_of_claim")
    assert st.status == Status.NOT_SATISFIED and st.document_id == out.document_id
    against = next(r for r in results if r.check_id == "claim_against_debtor")
    assert against.verdict == "FAIL" and "Narmada Basin Textiles" in against.reason
    assert _status(store, 1, elsewhere, "proof_of_claim") == Status.SATISFIED          # the first is untouched


def test_a_misfiled_document_found_by_its_identifiers_goes_to_a_person(env):
    """A Form C whose GSTIN names a creditor with no claim in the process it was filed in: not attached there, not
    carried to the creditor's own process either; a person says where it belongs."""
    world, store, engine = env
    (elsewhere, creditor), *_ = _satisfied(store, 1, "proof_of_claim")
    with store.tenant(1) as s:
        doc = s.get(Document, s.get(RequirementState, {"claim_id": elsewhere, "requirement_id": "proof_of_claim",
                                                       "tenant_id": 1}).document_id)
        theirs = {e.matter_id for e in s.scalars(select(Claim).where(Claim.creditor_id == creditor))}
        other = s.scalar(select(Matter.id).where(Matter.id.not_in(theirs)))
        sha = doc.sha256
    blob = next(p for p in (engine.blob_root / "1").rglob(sha))
    out = engine.process(1, engine.ingest(1, other, "scan.pdf", blob.read_bytes(), "email_ingest",
                                          datetime(2026, 9, 1, 9, 0)))
    with store.tenant(1) as s:
        misfiled = s.get(Document, out.document_id)
        assert misfiled.state == "needs_review" and misfiled.creditor_id is None
        assert "no claim in this process" in misfiled.match_detail["reason"]
        assert misfiled.match_detail["candidates"] == [creditor]
    assert any(i.kind == "document_owner" and i.document_id == out.document_id and i.matter_id == other
               for i in engine.queue(1))


def test_an_attestation_must_name_the_claim_for_evidence_of_one_process(env):
    """'Verified out of band' for a creditor is creditor-wide evidence; for a proof of claim it must name the claim."""
    world, store, engine = env
    _, creditor = next((c, k) for c, k in _satisfied(store, 1, "bank_details"))
    _, new = _new_process(store, 1, creditor)
    with store.tenant(1) as s:
        s.add(Attestation(creditor_id=creditor, requirement_id="proof_of_claim", verified_by="lead@firm",
                          verified_on=date(2026, 9, 1), note="register search"))
    engine.refresh(1)
    assert _status(store, 1, new, "proof_of_claim") == Status.MISSING
    with store.tenant(1) as s:
        s.add(Attestation(creditor_id=creditor, claim_id=new, requirement_id="proof_of_claim",
                          verified_by="lead@firm", verified_on=date(2026, 9, 1), note="register search"))
    engine.refresh(1)
    assert _status(store, 1, new, "proof_of_claim") == Status.VERIFIED_OUT_OF_BAND

"""Ground truth: what each check and requirement should say, computed from the generator's own record of what it
wrote, with logic independent of the engine. The status policy is shared on purpose: it is the specification the
engine is measured against, and it has its own unit tests."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from ..model import CheckResult, Severity, Status, Tier, Verdict
from ..pack import Pack, load_pack
from ..status import decide
from .world import Claim, Upload, World

KEY_DATE = {"authority_letter": "effective_date", "charge_certificate": "registration_date",
            "bank_mandate": "letter_date"}


def _d(s: str) -> date:
    return date.fromisoformat(s)


def _years_before(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year - years)
    except ValueError:
        return d.replace(year=d.year - years, day=28)


def _root(world: World, up: Upload) -> int:
    return up.truth.duplicate_of or up.id


def _copies(world: World, up: Upload, scope: str = "matter") -> list[Upload]:
    """Every upload of the same file that tells how and when this evidence arrived: in its own process, for evidence
    of one process; anywhere in the tenant, for evidence shared across processes."""
    root = _root(world, up)
    return [u for u in world.uploads if _root(world, u) == root and u.tenant_id == up.tenant_id
            and (scope == "creditor" or u.matter_id == up.matter_id)]


def evidence(world: World, tenant_id: int, doc_type: str) -> list[Upload]:
    """The file as evidence in each process it was filed in: one per (process, file), stood for by its first upload
    there (a copy carries the original's truth). A file filed in two processes is evidence twice, once in each."""
    groups: dict[tuple[int, int], list[Upload]] = {}
    for u in world.uploads:
        if u.tenant_id == tenant_id and u.truth.doc_type == doc_type and u.truth.variant != "wrong_tenant":
            groups.setdefault((u.matter_id, _root(world, u)), []).append(u)
    return [min(ups, key=lambda u: (u.uploaded_at, u.id)) for ups in groups.values()]


def current_document(world: World, e: Claim, doc_type: str, trustee_cover: bool,
                     scope: str | None = None) -> tuple[Upload | None, bool]:
    """The document that should decide the requirement: evidence filed in the claim's own process (anywhere in the
    tenant for a type the pack shares across processes); a creditor's own document beats a trustee's; among several,
    the most recent by the type's key date, then one filed in the claim's own process, then the first to arrive.
    Returns (the upload standing for it, via_trustee)."""
    scope = scope or load_pack().doc_types[doc_type].evidence_scope
    pool_all = [u for u in evidence(world, e.tenant_id, doc_type) if scope == "creditor" or u.matter_id == e.matter_id]
    direct = [u for u in pool_all if u.truth.owner_creditor_id == e.creditor_id]
    trustee = [u for u in pool_all if trustee_cover and e.creditor_id in u.truth.covers
               and u.truth.owner_creditor_id != e.creditor_id]
    pool, via_trustee = (direct, False) if direct else (trustee, bool(trustee))
    if not pool:
        return None, False
    key = KEY_DATE.get(doc_type)
    best = max(pool, key=lambda u: (u.truth.fields.get(key, "") if key else "", u.matter_id == e.matter_id,
                                    u.uploaded_at, u.id))
    return best, via_trustee


def truth_check(pack: Pack, world: World, check_id: str, rid: str, e: Claim, up: Upload,
                via_trustee: bool, resolved: bool = False) -> CheckResult:
    """`resolved`: the verdict once a careful person has looked, where the engine must abstain. A hostile document's
    clauses are judged on what they say; a field the document contradicts itself about is a FAIL (contradictory
    evidence is not accepted; a corrected document is requested)."""
    spec = pack.checks[check_id]
    t = up.truth
    f = t.fields
    as_of = world.as_of
    creditor = world.creditor(e.creditor_id)
    matter = world.matter(e.matter_id)
    copies = _copies(world, up, pack.doc_types[pack.requirements[rid].doc_type].evidence_scope)

    def res(v: Verdict, lapsed: bool = False) -> CheckResult:
        return CheckResult(check_id=check_id, requirement_id=rid, verdict=v, severity=spec.severity, tier=spec.tier,
                           reason="truth", lapsed=lapsed)

    def ok(cond: bool, lapsed: bool = False) -> CheckResult:
        return res(Verdict.PASS if cond else Verdict.FAIL, lapsed=(lapsed and not cond))

    req = pack.requirements[rid]
    if check_id in req.check_applies and not req.check_applies[check_id].matches(e.tier, e.claim_type):
        return res(Verdict.SKIPPED)
    if spec.tier == Tier.MODEL and t.injection and not resolved:
        return res(Verdict.ABSTAIN)          # a hostile document must go to a person, whatever it says
    field = spec.params.get("field")
    if field and field in t.ambiguous:
        return res(Verdict.FAIL if resolved else Verdict.ABSTAIN)   # the document contradicts itself

    if check_id == "received_by_due_date":
        return ok(min(c.uploaded_at for c in copies).date() <= matter.claims_due)
    if check_id == "bank_channel_verified":
        return ok(any(c.channel == "staff_verified" for c in copies))
    if check_id == "claim_against_debtor":
        return ok(f["debtor_name"] == matter.name)
    if check_id == "claim_name_matches":
        return ok(f["creditor_name"] == creditor.legal_name)
    if check_id == "claim_within_books":
        return ok(Decimal(f["amount_claimed"]) <= Decimal(e.book_balance) * Decimal(str(1 + spec.params["tolerance"])))
    if check_id == "claim_words_match":
        return ok(f["amount_in_words"] == f["amount_claimed"])
    if check_id == "claim_not_time_barred":
        return ok(_d(f["default_date"]) >= _years_before(matter.commencement, 3))
    if check_id == "claim_dated_after_announcement":
        return ok(_d(f["claim_date"]) >= matter.announced)
    if check_id == "claim_gstin_valid":
        return ok(f["gstin_valid"] == "True")
    if check_id == "claim_pan_in_gstin":
        return ok(f["pan"] == f["gstin"][2:12])
    if check_id == "claim_lei_valid":
        return res(Verdict.SKIPPED) if not f.get("lei") else ok(f["lei_valid"] == "True")
    if check_id == "auth_creditor_matches":
        expected = world.creditor(t.owner_creditor_id).legal_name if via_trustee else creditor.legal_name
        return ok(f["creditor_name"] == expected)
    if check_id == "auth_current":
        return ok(_d(f["valid_until"]) >= as_of, lapsed=True)
    if check_id == "auth_renewal_due":
        if _d(f["valid_until"]) < as_of:
            return res(Verdict.SKIPPED)      # a lapse is reported once, by the expiry check
        return ok(_d(f["valid_until"]) >= as_of + timedelta(days=30))
    if check_id == "auth_issued_recently":
        return ok((as_of - _d(f["issue_date"])).days <= 400)
    if check_id == "auth_covers_voting":
        return ok(any(term in f["powers"].casefold() for term in spec.params["terms"]))
    if check_id == "charge_holder_matches":
        return ok(f["charge_holder"] == creditor.legal_name)
    if check_id == "charge_on_debtor":
        return ok(f["company"] == matter.name)
    if check_id == "charge_registrar_recognised":
        return ok(f["registrar"] in pack.registrar_offices)
    if check_id == "charge_covers_assets":
        return ok(any(term in f["assets"].casefold() for term in pack.asset_terms[e.facility_type]))
    if check_id == "charge_amount_covers":
        return ok(int(f["amount_secured"]) >= e.sanctioned)
    if check_id == "bank_ifsc_valid":
        return ok(f["ifsc_valid"] == "True")
    if check_id == "bank_account_on_file":
        return ok(f["account_on_file"] == "True")
    if check_id == "bank_holder_matches":
        return ok(f["account_holder"] == creditor.legal_name)
    if check_id == "bank_letter_recent":
        return ok((as_of - _d(f["letter_date"])).days <= 180)
    if check_id == "facility_borrower_is_debtor":
        return ok(f["borrower"] == matter.name)
    if check_id == "facility_signed":
        return ok(bool(t.signed))
    if spec.tier == Tier.MODEL:
        return ok(bool(t.clauses.get(check_id)))
    raise KeyError(f"no truth rule for {check_id}")


def truth_statuses(world: World, pack: Pack | None = None, resolved: bool = False) -> dict[tuple[int, str], dict]:
    """(claim id, requirement id) -> {status, upload_id, via_trustee, checks: {check_id: verdict}}. With `resolved`,
    the statuses once people have settled everything the engine must hand to them."""
    pack = pack or load_pack()
    out: dict[tuple[int, str], dict] = {}
    for e in world.claims:
        for rid, req in pack.requirements.items():
            applicable = req.applies_to.matches(e.tier, e.claim_type)
            scope = pack.doc_types[req.doc_type].evidence_scope
            up, via_trustee = current_document(world, e, req.doc_type, req.trustee_cover, scope) if applicable \
                else (None, False)
            # the world attests creditor-wide only; evidence of one process would need an attestation naming the claim
            attested = scope == "creditor" and any(a.tenant_id == e.tenant_id and a.creditor_id == e.creditor_id
                                                   and a.requirement_id == rid for a in world.attestations)
            results = None
            if up is not None:
                results = _skip_decided([truth_check(pack, world, cid, rid, e, up, via_trustee, resolved)
                                         for cid in req.checks])
            status, _ = decide(applicable=applicable, results=results, via_trustee=via_trustee, attested=attested,
                               hostile=bool(up and up.truth.injection) and not resolved)
            out[(e.id, rid)] = {"status": status, "upload_id": up.id if up else None, "via_trustee": via_trustee,
                                "checks": {r.check_id: r.verdict for r in results} if results else {}}
    return out


def _skip_decided(results: list[CheckResult]) -> list[CheckResult]:
    """The cost rule, as the specification: once a deterministic critical check fails, the outcome is decided and the
    model checks are not paid for. A model check that must go to a person anyway (a hostile document) stays ABSTAIN."""
    if not any(r.severity == Severity.CRITICAL and r.verdict == Verdict.FAIL and r.tier != Tier.MODEL for r in results):
        return results
    return [r.model_copy(update={"verdict": Verdict.SKIPPED})
            if r.tier == Tier.MODEL and r.verdict in (Verdict.PASS, Verdict.FAIL) else r for r in results]


def severity_of(pack: Pack, check_id: str) -> Severity:
    return pack.checks[check_id].severity


__all__ = ["truth_statuses", "current_document", "truth_check", "Status", "datetime"]

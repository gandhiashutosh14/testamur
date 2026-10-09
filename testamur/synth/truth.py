"""Ground truth: what each check and requirement should say, computed from the generator's own record of what it
wrote, with logic independent of the engine. The status policy is shared on purpose: it is the specification the
engine is measured against, and it has its own unit tests."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from ..ids import vat_valid
from ..model import CheckResult, Severity, Status, Tier, Verdict
from ..pack import Pack, load_pack
from ..status import decide
from .world import Engagement, Upload, World

KEY_DATE = {"insurance_certificate": "effective_date", "security_certificate": "issue_date",
            "bank_letter": "letter_date"}


def _d(s: str) -> date:
    return date.fromisoformat(s)


def _copies(world: World, up: Upload) -> list[Upload]:
    root = up.truth.duplicate_of or up.id
    return [u for u in world.uploads if u.id == root or u.truth.duplicate_of == root]


def current_document(world: World, e: Engagement, doc_type: str, group_cover: bool) -> tuple[Upload | None, bool]:
    """The document that should decide the requirement: a direct document beats group cover; among several, the
    most recent by the type's key date (then by upload time). Returns (upload, via_parent)."""
    originals = [u for u in world.uploads if u.tenant_id == e.tenant_id and u.truth.duplicate_of is None
                 and u.truth.doc_type == doc_type and u.truth.variant != "wrong_tenant"]
    direct = [u for u in originals if u.truth.owner_supplier_id == e.supplier_id]
    group = [u for u in originals if group_cover and e.supplier_id in u.truth.covers
             and u.truth.owner_supplier_id != e.supplier_id]
    pool, via_parent = (direct, False) if direct else (group, bool(group))
    if not pool:
        return None, False
    key = KEY_DATE.get(doc_type)
    best = max(pool, key=lambda u: (u.truth.fields.get(key, "") if key else "", u.uploaded_at, u.id))
    return best, via_parent


def truth_check(pack: Pack, world: World, check_id: str, rid: str, e: Engagement, up: Upload,
                via_parent: bool) -> CheckResult:
    spec = pack.checks[check_id]
    t = up.truth
    f = t.fields
    as_of = world.as_of
    supplier = world.supplier(e.supplier_id)
    copies = _copies(world, up)

    def res(v: Verdict, lapsed: bool = False) -> CheckResult:
        return CheckResult(check_id=check_id, requirement_id=rid, verdict=v, severity=spec.severity, tier=spec.tier,
                           reason="truth", lapsed=lapsed)

    def ok(cond: bool, lapsed: bool = False) -> CheckResult:
        return res(Verdict.PASS if cond else Verdict.FAIL, lapsed=(lapsed and not cond))

    req = pack.requirements[rid]
    if check_id in req.check_applies and not req.check_applies[check_id].matches(e.tier, e.engagement_type):
        return res(Verdict.SKIPPED)
    if spec.tier == Tier.MODEL and t.injection:
        return res(Verdict.ABSTAIN)          # a hostile document must go to a person, whatever it says
    field = spec.params.get("field")
    if field and field in t.ambiguous:
        return res(Verdict.ABSTAIN)          # the document contradicts itself; only a person can settle it

    if check_id == "received_before_start":
        return ok(min(c.uploaded_at for c in copies).date() <= e.start_date)
    if check_id == "bank_channel_verified":
        return ok(any(c.channel == "staff_verified" for c in copies))
    if check_id == "ins_name_matches":
        expected = world.supplier(t.owner_supplier_id).legal_name if via_parent else supplier.legal_name
        return ok(f["insured_name"] == expected)
    if check_id == "ins_limit_meets_tier":
        return ok(int(f["gl_limit"]) >= pack.tier_minimum_liability[e.tier])
    if check_id in ("ins_cover_current", "sec_not_expired"):
        return ok(_d(f["expiry_date"]) >= as_of, lapsed=True)
    if check_id == "ins_renewal_due":
        if _d(f["expiry_date"]) < as_of:
            return res(Verdict.SKIPPED)      # a lapse is reported once, by the expiry check
        return ok(_d(f["expiry_date"]) >= as_of + timedelta(days=30))
    if check_id == "ins_issued_recently":
        return ok((as_of - _d(f["issue_date"])).days <= 400)
    if check_id == "sec_org_matches":
        return ok(f["organisation"] == supplier.legal_name)
    if check_id == "sec_body_accredited":
        return ok(f["certification_body"] in pack.accredited_bodies)
    if check_id == "sec_scope_covers_service":
        return ok(any(term in f["scope"].casefold() for term in pack.service_terms[e.service_category]))
    if check_id == "bank_iban_valid":
        return ok(f["iban_valid"] == "True")
    if check_id == "bank_iban_on_file":
        return ok(f["iban_on_file"] == "True")
    if check_id == "bank_holder_matches":
        return ok(f["account_holder"] == supplier.legal_name)
    if check_id == "bank_letter_recent":
        return ok((as_of - _d(f["letter_date"])).days <= 180)
    if check_id == "tax_name_matches":
        return ok(f["legal_name"] == supplier.legal_name)
    if check_id == "tax_vat_format":
        return ok(vat_valid(f["vat_number"]))
    if check_id == "tax_lei_valid":
        return res(Verdict.SKIPPED) if not f.get("lei") else ok(f["lei_valid"] == "True")
    if check_id == "msa_signed":
        return ok(bool(t.signed))
    if spec.tier == Tier.MODEL:
        return ok(bool(t.clauses.get(check_id)))
    raise KeyError(f"no truth rule for {check_id}")


def truth_statuses(world: World, pack: Pack | None = None) -> dict[tuple[int, str], dict]:
    """(engagement id, requirement id) -> {status, upload_id, via_parent, checks: {check_id: verdict}}."""
    pack = pack or load_pack()
    out: dict[tuple[int, str], dict] = {}
    for e in world.engagements:
        for rid, req in pack.requirements.items():
            applicable = req.applies_to.matches(e.tier, e.engagement_type)
            up, via_parent = current_document(world, e, req.doc_type, req.group_cover) if applicable else (None, False)
            attested = any(a.tenant_id == e.tenant_id and a.supplier_id == e.supplier_id and a.requirement_id == rid
                           for a in world.attestations)
            results = None
            if up is not None:
                results = [truth_check(pack, world, cid, rid, e, up, via_parent) for cid in req.checks]
            status, _ = decide(applicable=applicable, results=results, via_parent=via_parent, attested=attested,
                               hostile=bool(up and up.truth.injection))
            out[(e.id, rid)] = {"status": status, "upload_id": up.id if up else None, "via_parent": via_parent,
                                "checks": {r.check_id: r.verdict for r in results} if results else {}}
    return out


def severity_of(pack: Pack, check_id: str) -> Severity:
    return pack.checks[check_id].severity


__all__ = ["truth_statuses", "current_document", "truth_check", "Status", "datetime"]

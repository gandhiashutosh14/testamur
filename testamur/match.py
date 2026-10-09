"""Whose document is this?

1. Identifiers with valid check digits (LEI, IBAN, company number, VAT number) are looked up across the tenant. One
   hit decides; hits on different suppliers abstain.
2. Otherwise the name on the document is scored against the suppliers engaged in the business unit it was uploaded
   to, plus their parent companies. A clear winner (high score and a margin over the runner-up) decides.
3. A strong name match outside that business unit is not taken: the file may have been misfiled, and a person
   should confirm. Nothing matches across tenants, by construction (the session cannot see other tenants).
4. If the uploader claimed a supplier (a supplier-portal upload) and the document belongs to someone else, it
   abstains: that is either a mistake or an attempt to pass off another entity's evidence.

Abstaining is cheap (a person looks); attaching evidence to the wrong supplier is the error that hurts, because
every status derived from it is then wrong in a way nobody sees."""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from .ids import compact
from .store import Engagement, Supplier, UploadRow
from .textnorm import name_similarity

ACCEPT = 92.0
MARGIN = 5.0
EXACT = 99.5         # equal after normalisation (case, punctuation, legal-form suffix)
NEAR = 80.0          # close enough that the document might be theirs: flagged for review, never attached

NAME_FIELD = {"insurance_certificate": "insured_name", "security_certificate": "organisation",
              "bank_letter": "account_holder", "tax_registration": "legal_name", "master_agreement": "supplier_party"}


@dataclass
class MatchResult:
    supplier_id: int | None
    method: str                     # identifier | name | none
    score: float = 0.0
    margin: float = 0.0
    reason: str = ""
    candidates: list[int] = field(default_factory=list)   # who it might belong to, when abstaining
    covers: list[int] = field(default_factory=list)       # subsidiaries a group document covers


def match(session: Session, doc_type: str, name: str | None, ids: dict[str, list[str]], upload: UploadRow,
          covered_names: list[str] | None = None) -> MatchResult:
    suppliers = list(session.scalars(select(Supplier)))
    result = _resolve(session, suppliers, name, ids, upload)
    if result.supplier_id is not None and upload.claimed_supplier_id is not None:
        claimed = upload.claimed_supplier_id
        if result.supplier_id != claimed:
            covered = _covered(suppliers, covered_names or [], result.supplier_id)
            if claimed not in covered:
                return MatchResult(None, "none", result.score, result.margin,
                                   f"uploaded on behalf of supplier {claimed} but the document belongs to "
                                   f"supplier {result.supplier_id}", candidates=[claimed, result.supplier_id])
    if result.supplier_id is not None and covered_names:
        result.covers = _covered(suppliers, covered_names, result.supplier_id)
    if result.supplier_id is None and upload.claimed_supplier_id is not None:
        result.candidates = sorted(set(result.candidates) | {upload.claimed_supplier_id})
    return result


def _resolve(session: Session, suppliers: list[Supplier], name: str | None, ids: dict[str, list[str]],
             upload: UploadRow) -> MatchResult:
    hits: set[int] = set()
    for key in ("lei", "iban", "company_number", "vat_number"):
        for value in ids.get(key, []):
            for s in suppliers:
                on_file = getattr(s, key)
                if on_file and compact(on_file) == compact(value):
                    hits.add(s.id)
    if len(hits) == 1:
        sid = hits.pop()
        return MatchResult(sid, "identifier", 100.0, 100.0, "matched by a validated identifier")
    if len(hits) > 1:
        return MatchResult(None, "none", reason="identifiers on the document point to different suppliers",
                           candidates=sorted(hits))
    if not name:
        return MatchResult(None, "none", reason="no supplier name or identifier found on the document")
    in_scope = scope(session, suppliers, upload.business_unit_id)
    scored = sorted(((name_similarity(name, s.legal_name), s.id) for s in suppliers if s.id in in_scope), reverse=True)
    best, second = (scored + [(0.0, None), (0.0, None)])[:2]
    if best[0] >= ACCEPT and best[0] - second[0] >= MARGIN:
        return MatchResult(best[1], "name", best[0], best[0] - second[0], "matched by name within the business unit")
    if best[0] >= EXACT > second[0]:
        return MatchResult(best[1], "name", best[0], best[0] - second[0],
                           "exact legal-name match within the business unit; a similar name scored lower")
    if best[0] >= ACCEPT:
        tied = [sid for score, sid in scored if score >= ACCEPT]
        claimed = upload.claimed_supplier_id
        if claimed in tied:
            return MatchResult(claimed, "name+claim", next(sc for sc, sid in scored if sid == claimed), 0.0,
                               "names tie inside the business unit; the uploading supplier's own claim decides")
        return MatchResult(None, "none", best[0], best[0] - second[0],
                           "two suppliers in this business unit match the name", candidates=[best[1], second[1]])
    outside = sorted(((name_similarity(name, s.legal_name), s.id) for s in suppliers if s.id not in in_scope),
                     reverse=True)
    if outside and outside[0][0] >= ACCEPT:
        return MatchResult(None, "none", outside[0][0], 0.0,
                           "the name matches a supplier of a different business unit; possibly misfiled",
                           candidates=[outside[0][1]])
    # Nobody clears the bar. Suppliers who come close are named as candidates, so their requirements show the
    # unresolved document instead of being judged on older evidence as if it did not exist.
    near = [sid for score, sid in scored[:2] if score >= NEAR]
    return MatchResult(None, "none", best[0], 0.0, "no supplier of this business unit has this name",
                       candidates=near)


def scope(session: Session, suppliers: list[Supplier], business_unit_id: int) -> set[int]:
    """The suppliers a name is compared with: those engaged in the business unit, and their parent companies."""
    engaged = {e.supplier_id for e in session.scalars(
        select(Engagement).where(Engagement.business_unit_id == business_unit_id))}
    by_id = {s.id: s for s in suppliers}
    return engaged | {by_id[x].parent_id for x in engaged if x in by_id and by_id[x].parent_id}


def covered(session: Session, names: list[str], parent_id: int) -> list[int]:
    """Subsidiaries a group document covers, once a person has said whose document it is."""
    return _covered(list(session.scalars(select(Supplier))), names, parent_id)


def _covered(suppliers: list[Supplier], names: list[str], parent_id: int) -> list[int]:
    """Subsidiaries a group document names, resolved among the parent's own subsidiaries only."""
    subs = [s for s in suppliers if s.parent_id == parent_id]
    out = []
    for n in names:
        scored = sorted(((name_similarity(n, s.legal_name), s.id) for s in subs), reverse=True)
        if scored and scored[0][0] >= ACCEPT:
            out.append(scored[0][1])
    return sorted(set(out))

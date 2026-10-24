"""Whose document is this?

1. Identifiers that validate (LEI and GSTIN by their check characters, CIN by its structure, a bank account as an IFSC
   and account number together) are looked up across the tenant. One hit decides; hits on different creditors
   abstain. For evidence that counts in one process only, the creditor found must have a claim in the process the
   file was filed in (or act as trustee for a holder who has): otherwise the file is possibly misfiled, and a person
   says where it belongs. A file never becomes evidence in another process because its creditor matched.
2. Otherwise the name on the document is scored against the creditors with a claim in the matter it was uploaded
   to, plus the debenture trustees acting for them. A clear winner (high score and a margin over the runner-up) decides.
3. A strong name match outside that matter is not taken: the file may have been misfiled, and a person
   should confirm. Nothing matches across tenants, by construction (the session cannot see other tenants).
4. If the uploader claimed a creditor (a creditor-portal upload) and the document belongs to someone else, it
   abstains: that is either a mistake or an attempt to pass off another entity's evidence.

Abstaining is cheap (a person looks); attaching evidence to the wrong creditor is the error that hurts, because
every status derived from it is then wrong in a way nobody sees."""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from .ids import compact
from .store import Claim, Creditor, UploadRow
from .textnorm import name_similarity

ACCEPT = 92.0
MARGIN = 5.0
EXACT = 99.5         # equal after normalisation (case, punctuation, legal-form suffix)
NEAR = 80.0          # close enough that the document might be theirs: flagged for review, never attached


@dataclass
class MatchResult:
    creditor_id: int | None
    method: str                     # identifier | name | none
    score: float = 0.0
    margin: float = 0.0
    reason: str = ""
    candidates: list[int] = field(default_factory=list)   # who it might belong to, when abstaining
    covers: list[int] = field(default_factory=list)       # holders a trustee's document covers


def match(session: Session, doc_type: str, name: str | None, ids: dict[str, list[str]], upload: UploadRow,
          covered_names: list[str] | None = None, matter_scoped: bool = True) -> MatchResult:
    creditors = list(session.scalars(select(Creditor)))
    result = _resolve(session, creditors, name, ids, upload)
    if matter_scoped and result.method == "identifier" and \
            result.creditor_id not in scope(session, creditors, upload.matter_id):
        who = next(c.legal_name for c in creditors if c.id == result.creditor_id)
        return MatchResult(None, "none", result.score, 0.0,
                           f"the identifiers name {who}, who has no claim in this process: possibly misfiled",
                           candidates=[result.creditor_id])
    if result.creditor_id is not None and upload.declared_creditor_id is not None:
        claimed = upload.declared_creditor_id
        if result.creditor_id != claimed:
            covered = _covered(creditors, covered_names or [], result.creditor_id)
            if claimed not in covered:
                return MatchResult(None, "none", result.score, result.margin,
                                   f"uploaded on behalf of creditor {claimed} but the document belongs to "
                                   f"creditor {result.creditor_id}", candidates=[claimed, result.creditor_id])
    if result.creditor_id is not None and covered_names:
        result.covers = _covered(creditors, covered_names, result.creditor_id)
    if result.creditor_id is None and upload.declared_creditor_id is not None:
        result.candidates = sorted(set(result.candidates) | {upload.declared_creditor_id})
    return result


def _resolve(session: Session, creditors: list[Creditor], name: str | None, ids: dict[str, list[str]],
             upload: UploadRow) -> MatchResult:
    hits: set[int] = set()
    for key in ("lei", "gstin", "cin", "account"):
        for value in ids.get(key, []):
            for s in creditors:
                on_file = f"{s.ifsc}/{s.account_number}" if key == "account" else getattr(s, key)
                if on_file and compact(on_file) == compact(value):
                    hits.add(s.id)
    if len(hits) == 1:
        sid = hits.pop()
        return MatchResult(sid, "identifier", 100.0, 100.0, "matched by a validated identifier")
    if len(hits) > 1:
        return MatchResult(None, "none", reason="identifiers on the document point to different creditors",
                           candidates=sorted(hits))
    if not name:
        return MatchResult(None, "none", reason="no creditor name or identifier found on the document")
    in_scope = scope(session, creditors, upload.matter_id)
    scored = sorted(((name_similarity(name, s.legal_name), s.id) for s in creditors if s.id in in_scope), reverse=True)
    best, second = (scored + [(0.0, None), (0.0, None)])[:2]
    if best[0] >= ACCEPT and best[0] - second[0] >= MARGIN:
        return MatchResult(best[1], "name", best[0], best[0] - second[0], "matched by name within the matter")
    if best[0] >= EXACT > second[0]:
        return MatchResult(best[1], "name", best[0], best[0] - second[0],
                           "exact legal-name match within the matter; a similar name scored lower")
    if best[0] >= ACCEPT:
        tied = [sid for score, sid in scored if score >= ACCEPT]
        claimed = upload.declared_creditor_id
        if claimed in tied:
            return MatchResult(claimed, "name+declared", next(sc for sc, sid in scored if sid == claimed), 0.0,
                               "names tie inside the matter; the uploader's own declaration decides")
        return MatchResult(None, "none", best[0], best[0] - second[0],
                           "two creditors in this matter match the name", candidates=[best[1], second[1]])
    outside = sorted(((name_similarity(name, s.legal_name), s.id) for s in creditors if s.id not in in_scope),
                     reverse=True)
    if outside and outside[0][0] >= ACCEPT:
        return MatchResult(None, "none", outside[0][0], 0.0,
                           "the name matches a creditor of a different matter; possibly misfiled",
                           candidates=[outside[0][1]])
    # Nobody clears the bar. Creditors who come close are named as candidates, so their requirements show the
    # unresolved document instead of being judged on older evidence as if it did not exist.
    near = [sid for score, sid in scored[:2] if score >= NEAR]
    return MatchResult(None, "none", best[0], 0.0, "no creditor of this matter has this name",
                       candidates=near)


def scope(session: Session, creditors: list[Creditor], matter_id: int) -> set[int]:
    """The creditors a name is compared with: those with a claim in the matter, and the trustees acting for them."""
    claiming = {e.creditor_id for e in session.scalars(
        select(Claim).where(Claim.matter_id == matter_id))}
    by_id = {s.id: s for s in creditors}
    return claiming | {by_id[x].trustee_id for x in claiming if x in by_id and by_id[x].trustee_id}


def covered(session: Session, names: list[str], trustee_id: int) -> list[int]:
    """Holders a trustee's document covers, once a person has said whose document it is."""
    return _covered(list(session.scalars(select(Creditor))), names, trustee_id)


def _covered(creditors: list[Creditor], names: list[str], trustee_id: int) -> list[int]:
    """Holders a trustee's document names, resolved among the holders that trustee acts for only."""
    subs = [s for s in creditors if s.trustee_id == trustee_id]
    out = []
    for n in names:
        scored = sorted(((name_similarity(n, s.legal_name), s.id) for s in subs), reverse=True)
        if scored and scored[0][0] >= ACCEPT:
            out.append(scored[0][1])
    return sorted(set(out))

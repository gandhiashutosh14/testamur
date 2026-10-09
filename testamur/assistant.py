"""An assistant for the buyer's staff, answering from the same evidence the engine judged.

It answers the way the engine checks: deterministically wherever it can. A question about a supplier's standing,
a date, a limit or what is wrong is answered from the engine's own statuses, verdicts and field readings, citing the
page and line of the current document. A question about what a contract says is answered from the engine's clause
verdict and from retrieval over the current documents' passages. Every quote it gives is checked to be really on
the cited page; anything it cannot support, it does not say, and with nothing left it says it cannot find it.

Filters come before ranking, never after: the tenant (the session cannot see any other), the supplier (resolved by
name inside the tenant, asking back when two names are equally close), and the current document behind each
requirement (a superseded certificate is never cited). Document text is data, never instructions: passages that
address automated readers are never quoted, and an answer drawn from such a document says so.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

import bm25s
from sqlalchemy import select
from sqlalchemy.orm import Session

from .engine import Engine
from .match import EXACT as EXACT_NAME
from .model import Extraction, Status, Verdict
from .modeltier import passages, quote_in
from .store import Document, Engagement, RequirementState, Supplier
from .textnorm import name_similarity, normalize_name

LABELS = {"insurance": "insurance", "security_certification": "security certification", "bank_details": "bank details",
          "tax_registration": "tax registration", "master_agreement": "master agreement"}
REQUIREMENT_WORDS = {
    "insurance": ("insurance", "insured", "insurer", "liability cover", "cover", "policy"),
    "security_certification": ("security", "iso", "27001", "isms", "certification", "certified"),
    "bank_details": ("bank", "iban", "account", "payment"),
    "tax_registration": ("tax", "vat", "lei", "registration"),
    "master_agreement": ("agreement", "contract", "msa", "clause"),
}
FIELDS = [   # (words in the question, requirement, field)
    (("expire", "expiry", "expires", "valid until", "run out", "lapse"), "insurance", "expiry_date"),
    (("expire", "expiry", "expires", "valid until", "run out", "lapse"), "security_certification", "expiry_date"),
    (("limit", "how much cover", "how much liability"), "insurance", "gl_limit"),
    (("insurer", "who insures", "underwriter"), "insurance", "insurer"),
    (("certification body", "certified by", "who certified"), "security_certification", "certification_body"),
    (("scope",), "security_certification", "scope"),
]
CLAUSES = {"clause_liability_cap": ("cap", "limitation of liability", "liability cap", "limit its liability"),
           "clause_audit_rights": ("audit",), "clause_data_protection": ("data protection", "personal data", "gdpr"),
           "clause_anti_assignment": ("assign", "assignment")}
STATUS_WORDS = ("in order", "compliant", "status", "valid", "okay", " ok", "satisfied", "covered", "good to go")
YES_NO = ("is ", "are ", "has ", "have ", "does ", "do ")      # "is X insured?" asks for a standing
FINDING_WORDS = ("why", "what is wrong", "what s wrong", "issue", "problem", "finding", "fix")
STATUS_PHRASE = {Status.SATISFIED: "satisfied", Status.SATISFIED_WITH_FINDINGS: "satisfied, with findings",
                 Status.NOT_SATISFIED: "not satisfied", Status.EXPIRED: "expired",
                 Status.COVERED_BY_PARENT: "covered by the parent company's group policy",
                 Status.VERIFIED_OUT_OF_BAND: "verified out of band", Status.NOT_REQUIRED: "not required",
                 Status.MISSING: "missing", Status.NEEDS_REVIEW: "waiting for a person to review"}
_EXPIRING = re.compile(r"\b(?:expir\w*|lapse\w*|run out)\b.*?\b(?:next|within|in)\s+(\d{1,3})\s+days?\b", re.I)


@dataclass
class Citation:
    document_id: int
    page: int | None
    quote: str


@dataclass
class Answer:
    text: str
    kind: str                    # field | status | findings | clause | list | search | pending | clarify | refuse
    citations: list[Citation] = field(default_factory=list)
    supplier_id: int | None = None
    requirement_id: str | None = None
    value: object = None                       # the structured value behind a field answer
    candidates: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class Assistant:
    def __init__(self, engine: Engine):
        self.engine, self.pack = engine, engine.pack

    def ask(self, tenant_id: int, question: str) -> Answer:
        q = " " + re.sub(r"\s+", " ", question.casefold()) + " "
        with self.engine.store.tenant(tenant_id) as s:
            m = _EXPIRING.search(q)
            if m:
                return self._expiring(s, int(m.group(1)))
            supplier, candidates = _resolve(s, question)
            if candidates:
                return Answer("Which supplier do you mean: " + " or ".join(candidates) + "?", "clarify",
                              candidates=candidates)
            if supplier is None:
                return Answer("I cannot find a supplier of that name in your organisation's records.", "refuse")
            # route on the question without the supplier's own name ("People Payments" is not about bank details)
            own = set(_plain(supplier.legal_name).split())
            q = " " + " ".join(w for w in _plain(question).split() if w not in own) + " "
            rid = _requirement(q)
            for words, req, fld in FIELDS:
                if (rid in (None, req)) and any(w in q for w in words):
                    return self._field(s, supplier, req, fld)
            clause = next((cid for cid, words in CLAUSES.items() if any(w in q for w in words)), None)
            if clause and (rid in (None, "master_agreement")):
                return self._clause(s, supplier, clause, question)
            if rid and any(w in q for w in FINDING_WORDS):
                return self._findings(s, supplier, rid)
            if rid and (any(w in q for w in STATUS_WORDS) or q.lstrip().startswith(YES_NO)):
                return self._status(s, supplier, rid)
            return self._search(s, supplier, question)

    # ------------------------------------------------------------------ answers from the engine's records
    def _states(self, s: Session, supplier: Supplier, rid: str) -> list[RequirementState]:
        engs = [e.id for e in s.scalars(select(Engagement).where(Engagement.supplier_id == supplier.id))]
        return list(s.scalars(select(RequirementState).where(RequirementState.engagement_id.in_(engs),
                                                             RequirementState.requirement_id == rid)
                              .order_by(RequirementState.engagement_id)))

    def _field(self, s: Session, supplier: Supplier, rid: str, fld: str) -> Answer:
        states = self._states(s, supplier, rid)
        docs = {st.document_id for st in states if st.document_id is not None}
        what = fld.replace("_", " ").replace("gl ", "general liability ")
        if not docs:
            if any(st.status == Status.NEEDS_REVIEW for st in states):
                return Answer(f"{supplier.legal_name}'s {LABELS[rid]} is waiting for a person: a document that may be "
                              "theirs has not been matched yet, so I will not answer from older evidence.", "pending",
                              supplier_id=supplier.id, requirement_id=rid)
            return Answer(f"There is no current {LABELS[rid]} document for {supplier.legal_name}.", "refuse",
                          supplier_id=supplier.id, requirement_id=rid)
        doc = s.get(Document, max(docs))           # several engagements share the same current document as a rule
        reading = (doc.fields or {}).get(fld)
        if not reading:
            clash = (doc.conflicts or {}).get(fld)
            if clash:                              # the document contradicts itself: show both, decide neither
                cites = [c for c in (self._verified(s, doc, x.get("page"), x["raw"]) for x in clash) if c]
                stated = "; ".join(f"'{x['raw']}' on page {x['page']}" for x in clash)
                return self._warn(doc, Answer(f"The current {LABELS[rid]} document for {supplier.legal_name} states "
                                              f"two different values for the {what}: {stated}. A person should settle "
                                              "which applies.", "pending", cites, supplier.id, rid))
            return Answer(f"The current {LABELS[rid]} document for {supplier.legal_name} does not state the {what} "
                          "clearly; a person should look at it.", "pending", supplier_id=supplier.id,
                          requirement_id=rid)
        if "(read by " in reading["line"]:                # a person's reading: cited as theirs, not as the page's
            cite = Citation(doc.id, reading.get("page"), reading["line"])
        else:
            cite = self._verified(s, doc, reading.get("page"), reading["line"])
        if cite is None:
            return Answer("I could not verify that reading against the document; a person should look at it.",
                          "refuse", supplier_id=supplier.id, requirement_id=rid)
        value = reading["value"]
        shown = (f"{value[1]} {int(float(value[0])):,}" if isinstance(value, list) else str(value))
        answer = Answer(f"{supplier.legal_name}: {what} is {shown}, on page {cite.page} of the current "
                        f"{LABELS[rid]} document.", "field", [cite], supplier.id, rid, value=value)
        if self._pending_for(s, supplier, rid):
            answer.warnings.append("A newer document that may be theirs is waiting for review; this answer is from "
                                   "the evidence accepted so far.")
        return self._warn(doc, answer)

    def _pending_for(self, s: Session, supplier: Supplier, rid: str) -> bool:
        doc_type = self.pack.requirements[rid].doc_type
        return any(supplier.id in (d.match_detail or {}).get("candidates", [])
                   for d in s.scalars(select(Document).where(Document.state == "needs_review",
                                                             Document.doc_type == doc_type)))

    def _status(self, s: Session, supplier: Supplier, rid: str) -> Answer:
        states = self._states(s, supplier, rid)
        if not states:
            return Answer(f"{supplier.legal_name} has no engagement that requires {LABELS[rid]}.", "refuse",
                          supplier_id=supplier.id, requirement_id=rid)
        lines, cites = [], []
        for st in states:
            e = s.get(Engagement, st.engagement_id)
            lines.append(f"engagement {e.id} ({e.tier} tier): {STATUS_PHRASE[Status(st.status)]}")
            if st.document_id is not None:
                doc = s.get(Document, st.document_id)
                first = self._first_line(s, doc)
                if first is not None:
                    cites.append(first)
        answer = Answer(f"{supplier.legal_name}, {LABELS[rid]}: " + "; ".join(lines) + ".", "status", cites,
                        supplier.id, rid, value=[st.status for st in states])
        docs = [s.get(Document, st.document_id) for st in states if st.document_id is not None]
        return self._warn(docs[0], answer) if docs else answer

    def _findings(self, s: Session, supplier: Supplier, rid: str) -> Answer:
        notes, cites = [], []
        for st in self._states(s, supplier, rid):
            _, results = self.engine._verdicts(s, st.tenant_id, st.engagement_id, rid)
            for r in results:
                if r.verdict in (Verdict.FAIL, Verdict.ABSTAIN):
                    notes.append(f"{r.check_id}: {r.verdict} ({r.reason})" + (f". {r.fix_hint}" if r.fix_hint else ""))
                    for ev in r.evidence:
                        if ev.document_id and ev.quote and ev.source != "reviewer":
                            doc = s.get(Document, ev.document_id)
                            c = self._verified(s, doc, ev.page, ev.quote)
                            if c is not None:
                                cites.append(c)
        if not notes:
            return Answer(f"Nothing is wrong with {supplier.legal_name}'s {LABELS[rid]}.", "findings",
                          supplier_id=supplier.id, requirement_id=rid)
        unique = list({(c.document_id, c.page, c.quote): c for c in cites}.values())
        return Answer(f"{supplier.legal_name}, {LABELS[rid]}: " + " | ".join(dict.fromkeys(notes)), "findings", unique,
                      supplier.id, rid)

    def _clause(self, s: Session, supplier: Supplier, clause: str, question: str) -> Answer:
        states = [st for st in self._states(s, supplier, "master_agreement") if st.document_id is not None]
        if not states:
            return Answer(f"There is no current master agreement with {supplier.legal_name}.", "refuse",
                          supplier_id=supplier.id, requirement_id="master_agreement")
        st = states[0]
        doc = s.get(Document, st.document_id)
        _, results = self.engine._verdicts(s, st.tenant_id, st.engagement_id, "master_agreement")
        verdict = next((r for r in results if r.check_id == clause), None)
        spec = self.pack.checks[clause]
        hits = self._retrieve(s, [doc], question + " " + spec.params["search"], k=1)
        cites = hits[:1]
        if verdict is None or verdict.verdict in (Verdict.ABSTAIN, Verdict.SKIPPED):
            said = "the engine has not decided whether it does; a person should read the passage below"
        else:
            said = ("it does" if verdict.verdict == Verdict.PASS else "it does not, as far as the engine can tell")
        claim = spec.params["claim"].rstrip(".")
        return self._warn(doc, Answer(f"Does the agreement with {supplier.legal_name} say that {claim[0].lower()}"
                                      f"{claim[1:]}? {said[0].upper()}{said[1:]}.", "clause", cites, supplier.id,
                                      "master_agreement", value=verdict.verdict if verdict else None))

    def _expiring(self, s: Session, days: int) -> Answer:
        today, cites, rows = self.engine.as_of, [], []
        horizon = today + timedelta(days=days)
        seen = set()
        for st in s.scalars(select(RequirementState).where(RequirementState.requirement_id == "insurance",
                                                           RequirementState.document_id.is_not(None))):
            if st.document_id in seen:
                continue
            seen.add(st.document_id)
            doc = s.get(Document, st.document_id)
            reading = (doc.fields or {}).get("expiry_date")
            if not reading:
                continue
            expiry = date.fromisoformat(reading["value"])
            if today <= expiry <= horizon:
                c = self._verified(s, doc, reading.get("page"), reading["line"])
                if c is not None:
                    name = s.get(Supplier, doc.supplier_id).legal_name
                    rows.append((expiry, name))
                    cites.append(c)
        if not rows:
            return Answer(f"No current insurance certificate expires in the next {days} days.", "list", value=[])
        rows.sort()
        return Answer(f"Insurance expiring in the next {days} days: " +
                      "; ".join(f"{n} on {d.isoformat()}" for d, n in rows) + ".", "list", cites,
                      value=[(d.isoformat(), n) for d, n in rows])

    def _search(self, s: Session, supplier: Supplier, question: str) -> Answer:
        docs = [s.get(Document, st.document_id) for rid in self.pack.requirements
                for st in self._states(s, supplier, rid) if st.document_id is not None]
        hits = self._retrieve(s, list({d.id: d for d in docs}.values()), question, k=1, min_overlap=2,
                              exclude=set(normalize_name(supplier.legal_name).split()))
        if not hits:
            return Answer(f"I cannot find that in {supplier.legal_name}'s current evidence.", "refuse",
                          supplier_id=supplier.id)
        return Answer(f"From {supplier.legal_name}'s current evidence: \"{hits[0].quote}\"", "search", hits,
                      supplier.id)

    # ------------------------------------------------------------------ evidence
    def _extraction(self, s: Session, doc: Document) -> Extraction:
        return self.engine._extraction(s, doc.tenant_id, doc.sha256)

    def _verified(self, s: Session, doc: Document, page: int | None, quote: str) -> Citation | None:
        """A citation only if the quote is really on that page (or anywhere, when no page is known)."""
        ex = self._extraction(s, doc)
        text = ex.text(page) if page else ex.text()
        q = quote.split(" (read by ")[0]
        if quote_in(q, text) or (len(q) < 20 and q.casefold() in text.casefold()):
            return Citation(doc.id, page, q)
        return None

    def _first_line(self, s: Session, doc: Document) -> Citation | None:
        ex = self._extraction(s, doc)
        for p in ex.pages:
            for line in p.lines:
                if line.text.strip():
                    return Citation(doc.id, p.number, line.text.strip())
        return None

    def _retrieve(self, s: Session, docs: list[Document], query: str, k: int = 3, min_overlap: int = 1,
                  exclude: set[str] | None = None) -> list[Citation]:
        """Hybrid ranking over the given documents' passages: BM25 on words and character trigrams (robust to OCR
        slips), fused by reciprocal rank. Passages that address automated readers are never candidates."""
        hostile = self.pack.injection_patterns()
        chunks: list[tuple[Document, int, str]] = []
        for d in docs:
            for p in self._extraction(s, d).pages:
                for text in passages([line.text for line in p.lines]):
                    if not any(h.search(text) for h in hostile):
                        chunks.append((d, p.number, text))
        if not chunks:
            return []
        words = _content_words(query, exclude or set())
        overlap = [len(words & _content_words(c[2], set())) for c in chunks]
        retriever = bm25s.BM25()
        retriever.index(bm25s.tokenize([c[2] for c in chunks], stopwords="en", show_progress=False), show_progress=False)
        ids, _ = retriever.retrieve(bm25s.tokenize([query], stopwords="en", show_progress=False),
                                    k=len(chunks), show_progress=False)
        bm25_rank = {int(i): r for r, i in enumerate(ids[0])}
        grams = _trigrams(query)
        tri = sorted(range(len(chunks)), key=lambda i: -_jaccard(grams, _trigrams(chunks[i][2])))
        tri_rank = {i: r for r, i in enumerate(tri)}
        fused = sorted(range(len(chunks)), key=lambda i: -(1 / (60 + bm25_rank.get(i, len(chunks)))
                                                            + 1 / (60 + tri_rank[i])))
        out = []
        for i in fused:
            if overlap[i] < min_overlap:
                continue
            d, page, text = chunks[i]
            out.append(Citation(d.id, page, text))
            if len(out) == k:
                break
        return out

    def _warn(self, doc: Document, answer: Answer) -> Answer:
        if doc is not None and doc.hostile and not doc.cleared:
            answer.warnings.append("This document carries text aimed at automated readers; a person should review "
                                   "it before anyone relies on it.")
        return answer


def _requirement(q: str) -> str | None:
    hits = {rid: sum(w in q for w in words) for rid, words in REQUIREMENT_WORDS.items()}
    best = max(hits.values())
    top = [rid for rid, n in hits.items() if n == best]
    return top[0] if best and len(top) == 1 else None


# A name in a question: capitalised words, with the small words and punctuation company names carry.
_NAME_SPAN = re.compile(r"[A-Z][\w&.'()-]*(?:\s+(?:of|and|the|&|[A-Z(][\w&.'()-]*))*")
_QUESTION_WORDS = frozenset("when what which who whose is are does do has have how why show list tell give can "
                            "the a an i we".split())


def _resolve(s: Session, question: str) -> tuple[Supplier | None, list[str]]:
    """The supplier the question names, inside the tenant. The name is taken from the question's capitalised words
    and compared whole, so 'Glenmore Facilities Ltd' never resolves to 'Glenmore Holdings plc'. A first word on its
    own ('Glenmore') resolves only if one supplier has it; two or more are asked back, as are two names that fit
    equally well. Nothing close enough is not found."""
    suppliers = list(s.scalars(select(Supplier)))
    spans = []
    for m in _NAME_SPAN.finditer(question):
        words = m.group().replace("'s", "").replace("’s", "").split()
        while words and words[0].casefold().strip("'") in _QUESTION_WORDS:
            words = words[1:]
        if words:
            spans.append(" ".join(words))
    if not spans:                                  # no capitals: only a full name written out counts
        q = _plain(question)
        named = [x for x in suppliers if normalize_name(x.legal_name) and f" {normalize_name(x.legal_name)} " in q]
        return (max(named, key=lambda x: len(x.legal_name)), []) if named else (None, [])
    best_score, best, close = 0.0, None, []
    for span in spans:
        if len(span.split()) == 1:
            first = [x for x in suppliers if normalize_name(x.legal_name).split()[:1] == [span.casefold()]]
            if len(first) == 1 and len(span) >= 4:
                return first[0], []
            if len(first) > 1:
                return None, sorted(x.legal_name for x in first)[:4]
            continue
        scored = sorted(((name_similarity(span, x.legal_name), x) for x in suppliers), key=lambda t: -t[0])
        if scored and scored[0][0] > best_score:
            best_score, best = scored[0]
            close = [x.legal_name for sc, x in scored[1:4] if sc >= 90 and best_score - sc < 3]
    if best is None or best_score < 90:
        return None, []
    if close and best_score < EXACT_NAME:
        return None, [best.legal_name] + close
    return best, []


_STOP = frozenset("what when where which does have with from that this their there about please tell show give "
                  "they them supplier suppliers company document documents evidence current".split())


def _plain(text: str) -> str:
    return " " + " ".join(re.sub(r"[^0-9a-z ]", " ", text.casefold()).split()) + " "


def _content_words(text: str, exclude: set[str]) -> set[str]:
    return {w for w in _plain(text).split() if len(w) >= 4 and w not in _STOP and w not in exclude}


def _trigrams(text: str) -> set[str]:
    t = " " + re.sub(r"\s+", " ", text.casefold()) + " "
    return {t[i:i + 3] for i in range(len(t) - 2)}


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


"""The tools an agent may call, and the gateway that is the only way it reaches the evidence.

The gateway runs every call inside the run's tenant session (so another tenant does not exist), within the claim's
evidence (a document that cannot be this claim's evidence is refused, whatever id the model sends), within the
agent's role (the investigator and the verifier hold different tools), and within a call budget. Every call, allowed
or refused, is an event in the run's trace. Documents that address automated readers are withheld: the tool says so,
and no text of theirs reaches a model. All tools read; none writes.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import bm25s
from sqlalchemy import select

from ..engine import Engine
from ..modeltier import passages
from ..store import Claim, Creditor, Document, Matter, RequirementState, UploadRow
from ..telemetry import span

MAX_TEXT = 1800                 # characters of document text one call returns
WITHHELD = ("withheld: this document carries text aimed at automated readers; a person must read it, and no model "
            "is shown its text")

DESCRIPTIONS = {
    "claim_summary": ("The claim under investigation: the process (corporate debtor and its dates), the creditor, the "
                      "amounts in the debtor's books, and each requirement with the engine's status.", {}),
    "list_evidence": ("The documents that can be this claim's evidence for a requirement (or all of them): id, type, "
                      "pages, which one is current, and any that wait for a person.",
                      {"requirement": "optional requirement id"}),
    "read_page": ("The text of one page of a document, line by line.", {"document_id": "int", "page": "int"}),
    "search": ("The passages of a document that best match some words, with their pages.",
               {"document_id": "int", "query": "words to look for"}),
    "engine_checks": ("The rule engine's verdicts on the current document of a requirement, with the evidence each "
                      "read.", {"requirement": "requirement id"}),
    "document_history": ("Every document of a requirement's type for this creditor in the firm, in any process, with "
                         "its process and dates: for spotting an older version or another process's document.",
                         {"requirement": "requirement id"}),
    "provenance": ("How a document arrived: each upload's process, channel, date and file name.",
                   {"document_id": "int"}),
}
ROLE_TOOLS = {
    "investigator": ("claim_summary", "list_evidence", "read_page", "search", "engine_checks"),
    "verifier": ("claim_summary", "list_evidence", "read_page", "search", "engine_checks", "document_history",
                 "provenance"),
}


class ToolDenied(Exception):
    """The call is outside what this agent may do: the reason goes back to the agent and into the trace."""


@dataclass
class ToolEvent:
    role: str
    step: int
    tool: str
    args: dict
    outcome: str                    # ok | denied | error
    reason: str = ""
    result_chars: int = 0
    ms: float = 0.0


@dataclass
class ToolGateway:
    engine: Engine
    tenant_id: int
    claim_id: int
    role: str
    max_calls: int = 12
    events: list[ToolEvent] = field(default_factory=list)
    step: int = 0

    # ------------------------------------------------------------------ the gate
    def describe(self) -> list[dict]:
        return [{"name": t, "description": DESCRIPTIONS[t][0], "args": DESCRIPTIONS[t][1]}
                for t in ROLE_TOOLS[self.role]]

    def call(self, tool: str, args: dict | None) -> dict:
        args = dict(args or {})
        t0 = time.perf_counter()
        try:
            if len([e for e in self.events if e.outcome != "denied"]) >= self.max_calls:
                raise ToolDenied(f"the {self.role} has used its {self.max_calls} tool calls")
            if tool not in DESCRIPTIONS:
                raise ToolDenied(f"there is no tool called {tool!r}")
            if tool not in ROLE_TOOLS[self.role]:
                raise ToolDenied(f"the {self.role} does not hold the tool {tool!r}")
            result = getattr(self, f"_{tool}")(**_typed(tool, args))
            event = ToolEvent(self.role, self.step, tool, args, "ok", result_chars=len(str(result)))
        except ToolDenied as exc:
            result, event = {"error": str(exc)}, ToolEvent(self.role, self.step, tool, args, "denied", str(exc))
        except (KeyError, ValueError, TypeError) as exc:
            result = {"error": f"the call could not be answered: {exc}"}
            event = ToolEvent(self.role, self.step, tool, args, "error", str(exc))
        event.ms = round(1000 * (time.perf_counter() - t0), 1)
        self.events.append(event)
        with span("tool", tenant_id=self.tenant_id, claim_id=self.claim_id, role=self.role, tool=tool,
                  outcome=event.outcome, reason=event.reason[:80] or None, steps=self.step):
            pass                              # a span per call: who called what, and whether it was allowed
        return result

    # ------------------------------------------------------------------ what the claim's evidence is
    def _scope(self, s) -> tuple[Claim, Creditor, dict[int, Document]]:
        """The documents that can be this claim's evidence: the creditor's (and its trustee's, where a trustee's
        document covers it) in the claim's process, or in any process for types the pack shares across them, and the
        unresolved documents naming the creditor as a candidate."""
        claim = s.get(Claim, self.claim_id)
        if claim is None:
            raise ToolDenied("no such claim in this firm")
        creditor = s.get(Creditor, claim.creditor_id)
        owners = {creditor.id} | ({creditor.trustee_id} if creditor.trustee_id else set())
        docs = {}
        for d in s.scalars(select(Document).where(Document.doc_type.is_not(None))):
            spec = self.engine.pack.doc_types.get(d.doc_type)
            if spec is None:
                continue
            here = spec.evidence_scope == "creditor" or d.matter_id == claim.matter_id
            mine = d.creditor_id in owners or creditor.id in (d.covers or []) or \
                creditor.id in (d.match_detail or {}).get("candidates", [])
            if here and mine:
                docs[d.id] = d
        return claim, creditor, docs

    def _document(self, s, document_id: int) -> Document:
        _, _, docs = self._scope(s)
        if document_id not in docs:
            raise ToolDenied(f"document {document_id} is not this claim's evidence")
        return docs[document_id]

    def _pages(self, s, doc: Document):
        return self.engine._extraction(s, self.tenant_id, doc.sha256).pages

    # ------------------------------------------------------------------ the tools
    def _claim_summary(self) -> dict:
        with self.engine.store.tenant(self.tenant_id) as s:
            claim, creditor, _ = self._scope(s)
            m = s.get(Matter, claim.matter_id)
            states = {st.requirement_id: st for st in s.scalars(select(RequirementState).where(
                RequirementState.claim_id == claim.id))}
            return {"claim": claim.id, "creditor": creditor.legal_name, "corporate_debtor": m.name,
                    "commencement": m.commencement.isoformat(), "public_announcement": m.announced.isoformat(),
                    "last_date_for_claims": m.claims_due.isoformat(), "claim_type": claim.claim_type,
                    "facility": claim.facility_type, "book_balance_rupees": claim.book_balance,
                    "sanctioned_rupees": claim.sanctioned,
                    "requirements": {rid: {"status": st.status, "document": st.document_id,
                                           "reasons": [r[:160] for r in (st.reasons or [])][:6]}
                                     for rid, st in sorted(states.items())}}

    def _list_evidence(self, requirement: str | None = None) -> dict:
        with self.engine.store.tenant(self.tenant_id) as s:
            claim, _, docs = self._scope(s)
            current = {st.requirement_id: st.document_id for st in s.scalars(select(RequirementState).where(
                RequirementState.claim_id == claim.id))}
            wanted = None
            if requirement is not None:
                if requirement not in self.engine.pack.requirements:
                    raise ToolDenied(f"there is no requirement {requirement!r}")
                wanted = self.engine.pack.requirements[requirement].doc_type
            out = []
            for d in sorted(docs.values(), key=lambda d: d.id):
                if wanted and d.doc_type != wanted:
                    continue
                for_req = [rid for rid, did in current.items() if did == d.id]
                state = ("current for " + ", ".join(for_req)) if for_req else (
                    "waiting for a person to say whose it is" if d.state == "needs_review" else
                    "not current (an older or other version)")
                out.append({"document": d.id, "type": d.doc_type, "pages": len(self._pages(s, d)), "state": state,
                            "quarantined": bool(d.hostile and not d.cleared)})
            return {"documents": out}

    def _read_page(self, document_id: int, page: int) -> dict:
        with self.engine.store.tenant(self.tenant_id) as s:
            doc = self._document(s, document_id)
            if doc.hostile:
                return {"document": doc.id, "withheld": WITHHELD}
            pages = self._pages(s, doc)
            p = next((x for x in pages if x.number == page), None)
            if p is None:
                raise ValueError(f"document {doc.id} has {len(pages)} page(s)")
            text = "\n".join(f"{i}: {line.text}" for i, line in enumerate(p.lines, 1))
            return {"document": doc.id, "page": page, "of": len(pages), "lines": text[:MAX_TEXT],
                    "truncated": len(text) > MAX_TEXT}

    def _search(self, document_id: int, query: str) -> dict:
        with self.engine.store.tenant(self.tenant_id) as s:
            doc = self._document(s, document_id)
            if doc.hostile:
                return {"document": doc.id, "withheld": WITHHELD}
            chunks = [(p.number, text) for p in self._pages(s, doc) for text in passages([x.text for x in p.lines])]
            if not chunks:
                return {"document": doc.id, "passages": []}
            retriever = bm25s.BM25()
            retriever.index(bm25s.tokenize([c[1] for c in chunks], stopwords="en", show_progress=False),
                            show_progress=False)
            ids, _ = retriever.retrieve(bm25s.tokenize([query], stopwords="en", show_progress=False),
                                        k=min(3, len(chunks)), show_progress=False)
            return {"document": doc.id, "passages": [{"page": chunks[int(i)][0], "text": chunks[int(i)][1][:500]}
                                                     for i in ids[0]]}

    def _engine_checks(self, requirement: str) -> dict:
        if requirement not in self.engine.pack.requirements:
            raise ToolDenied(f"there is no requirement {requirement!r}")
        with self.engine.store.tenant(self.tenant_id) as s:
            claim, _, _ = self._scope(s)
            st, results = self.engine._verdicts(s, self.tenant_id, claim.id, requirement)
            if st is None:
                return {"requirement": requirement, "status": "not computed"}
            return {"requirement": requirement, "status": st.status, "document": st.document_id,
                    "checks": [{"check": r.check_id, "severity": r.severity.value, "verdict": r.verdict.value,
                                "reason": r.reason[:200],
                                "evidence": [{"page": e.page, "quote": (e.quote or "")[:200]} for e in r.evidence
                                             if e.quote and e.source != "reviewer"][:2]} for r in results]}

    def _document_history(self, requirement: str) -> dict:
        if requirement not in self.engine.pack.requirements:
            raise ToolDenied(f"there is no requirement {requirement!r}")
        doc_type = self.engine.pack.requirements[requirement].doc_type
        key = self.engine.pack.doc_types[doc_type].key_date
        with self.engine.store.tenant(self.tenant_id) as s:
            claim, creditor, _ = self._scope(s)
            st = s.get(RequirementState, {"claim_id": claim.id, "requirement_id": requirement,
                                          "tenant_id": self.tenant_id})
            owners = {creditor.id} | ({creditor.trustee_id} if creditor.trustee_id else set())
            out = []
            for d in s.scalars(select(Document).where(Document.doc_type == doc_type).order_by(Document.id)):
                if d.creditor_id not in owners and creditor.id not in (d.covers or []) and \
                        creditor.id not in (d.match_detail or {}).get("candidates", []):
                    continue
                out.append({"document": d.id, "process": s.get(Matter, d.matter_id).name,
                            "this_claims_process": d.matter_id == claim.matter_id, "state": d.state,
                            key or "date": ((d.fields or {}).get(key) or {}).get("value") if key else None,
                            "current_for_this_claim": st is not None and st.document_id == d.id})
            return {"requirement": requirement, "documents": out}

    def _provenance(self, document_id: int) -> dict:
        with self.engine.store.tenant(self.tenant_id) as s:
            doc = self._document(s, document_id)
            ups = s.scalars(select(UploadRow).where(UploadRow.sha256 == doc.sha256).order_by(UploadRow.uploaded_at))
            return {"document": doc.id, "uploads": [
                {"process": s.get(Matter, u.matter_id).name, "this_document": u.matter_id == doc.matter_id,
                 "channel": u.channel, "at": u.uploaded_at.isoformat(timespec="minutes"),
                 "file_name": u.filename[:80]} for u in ups]}


def _typed(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    """Arguments as the tool takes them: ids as integers, words as text, nothing else."""
    out: dict[str, Any] = {}
    for name in DESCRIPTIONS[tool][1]:
        if name not in args:
            continue
        value = args[name]
        if name in ("document_id", "page"):
            if isinstance(value, bool) or not isinstance(value, int | str) or not str(value).strip().isdigit():
                raise ValueError(f"{name} must be a whole number")
            value = int(value)
        elif value is not None:
            value = str(value)[:200]
        out[name] = value
    missing = [n for n, kind in DESCRIPTIONS[tool][1].items() if n not in out and not kind.startswith("optional")]
    if missing:
        raise ValueError(f"missing {', '.join(missing)}")
    return out

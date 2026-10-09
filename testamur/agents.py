"""Agents act for one tenant, through a key, within the tools that tenant granted, and change nothing on their own.

- **Keys.** A key names the tenant and the agent; only its hash is stored. Looking a key up is the one read that
  must happen before any tenant is known, so it is done in the system session, and nothing else is.
- **Catalogue.** A tenant grants and revokes tools by name. A tool not granted is not listed and is refused if
  called, checked on every call, so a revocation takes effect immediately.
- **Reads** paginate with a cursor and run a fixed number of queries per page, however many rows a tenant has: an
  agent can call a tool far more often than a person clicks, so a query per row would not survive.
- **Writes are proposals.** An agent proposes a decision (a check verdict, a field reading, a document decision); a
  person confirms or declines it. A confirmed proposal is applied through the same engine call a person would make,
  under the person's name with the agent's recorded beside it, and audited. Proposals expire after a week.
"""
from __future__ import annotations

import hashlib
import secrets
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import select

from .assistant import Assistant
from .engine import Engine
from .model import Verdict
from .store import (
    ApiKey,
    AuditRow,
    Document,
    Engagement,
    Hold,
    Proposal,
    RequirementState,
    Store,
    Supplier,
    Task,
    ToolGrant,
    utcnow,
)

READ_TOOLS = ("list_engagements", "get_requirement", "review_queue", "open_tasks", "ask", "proposal_status")
PROPOSE_TOOLS = ("propose_check_verdict", "propose_field_reading", "propose_document_decision")
TOOLS = READ_TOOLS + PROPOSE_TOOLS
PROPOSAL_TTL = timedelta(days=7)
MAX_PAGE = 100


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


# ---------------------------------------------------------------------------------------------- keys and catalogue

def create_key(store: Store, tenant_id: int, name: str) -> str:
    """A new key for an agent of this tenant. The key is shown once; only its hash is kept."""
    key = "tsk_" + secrets.token_urlsafe(24)
    with store.tenant(tenant_id) as s:
        s.add(ApiKey(name=name, key_hash=_hash(key)))
        s.add(AuditRow(actor="admin", action="agent_key_created", target=f"agent:{name}", detail={}))
    return key


def authenticate(store: Store, key: str) -> tuple[int, str]:
    """The tenant and agent name a key stands for. The only cross-tenant read: the key decides the tenant."""
    with store.system() as s:
        row = s.scalar(select(ApiKey).where(ApiKey.key_hash == _hash(key or ""), ApiKey.revoked_at.is_(None)))
        if row is None:
            raise PermissionError("unknown or revoked key")
        return row.tenant_id, row.name


def grant(store: Store, tenant_id: int, tool: str, by: str) -> None:
    if tool not in TOOLS:
        raise KeyError(f"unknown tool {tool}")
    with store.tenant(tenant_id) as s:
        row = s.scalar(select(ToolGrant).where(ToolGrant.tool == tool))
        if row is None:
            s.add(ToolGrant(tool=tool, granted_by=by))
        else:
            row.revoked_at, row.granted_by, row.granted_at = None, by, utcnow()
        s.add(AuditRow(actor=by, action="tool_granted", target=f"tool:{tool}", detail={}))


def revoke(store: Store, tenant_id: int, tool: str, by: str) -> None:
    with store.tenant(tenant_id) as s:
        row = s.scalar(select(ToolGrant).where(ToolGrant.tool == tool, ToolGrant.revoked_at.is_(None)))
        if row is not None:
            row.revoked_at = utcnow()
            s.add(AuditRow(actor=by, action="tool_revoked", target=f"tool:{tool}", detail={}))


def granted(store: Store, tenant_id: int) -> set[str]:
    with store.tenant(tenant_id) as s:
        return {g.tool for g in s.scalars(select(ToolGrant).where(ToolGrant.revoked_at.is_(None)))}


def require(store: Store, tenant_id: int, tool: str) -> None:
    if tool not in granted(store, tenant_id):
        raise PermissionError(f"the tool {tool} is not granted for this tenant")


# ---------------------------------------------------------------------------------------------- reads

def list_engagements(store: Store, tenant_id: int, cursor: int = 0, limit: int = 25) -> dict:
    """A page of engagements with each requirement's status and any purchasing hold. Four queries, whatever the
    page size: engagements, their suppliers, their statuses, their holds."""
    limit = max(1, min(limit, MAX_PAGE))
    with store.tenant(tenant_id) as s:
        engs = s.scalars(select(Engagement).where(Engagement.id > cursor).order_by(Engagement.id).limit(limit)).all()
        ids = [e.id for e in engs]
        names = {x.id: x.legal_name for x in s.scalars(select(Supplier).where(
            Supplier.id.in_({e.supplier_id for e in engs})))}
        states: dict[int, dict] = defaultdict(dict)
        for st in s.scalars(select(RequirementState).where(RequirementState.engagement_id.in_(ids))):
            states[st.engagement_id][st.requirement_id] = st.status
        held = {h.engagement_id for h in s.scalars(select(Hold).where(Hold.engagement_id.in_(ids),
                                                                     Hold.released_at.is_(None)))}
    items = [{"engagement": e.id, "supplier": names.get(e.supplier_id), "tier": e.tier, "type": e.engagement_type,
              "statuses": states.get(e.id, {}), "on_hold": e.id in held} for e in engs]
    return {"items": items, "next_cursor": ids[-1] if len(ids) == limit else None}


def get_requirement(engine: Engine, tenant_id: int, engagement: int, requirement: str) -> dict:
    with engine.store.tenant(tenant_id) as s:
        st, results = engine._verdicts(s, tenant_id, engagement, requirement)
    if st is None:
        raise KeyError("no such engagement and requirement for this tenant")
    return {"engagement": engagement, "requirement": requirement, "status": st.status, "document": st.document_id,
            "reasons": st.reasons,
            "checks": [{"check": r.check_id, "verdict": r.verdict.value, "reason": r.reason,
                        "evidence": [{"page": e.page, "quote": e.quote} for e in r.evidence if e.quote],
                        "fix": r.fix_hint} for r in results]}


def review_queue(engine: Engine, tenant_id: int, cursor: int = 0, limit: int = 25) -> dict:
    items = engine.queue(tenant_id)
    limit = max(1, min(limit, MAX_PAGE))
    page = items[cursor:cursor + limit]
    return {"items": [{"kind": i.kind, "document": i.document_id, "engagement": i.engagement_id,
                       "requirement": i.requirement_id, "check": i.check_id, "reason": i.reason,
                       "blocking": i.blocking} for i in page],
            "next_cursor": cursor + limit if cursor + limit < len(items) else None, "total": len(items)}


def open_tasks(store: Store, tenant_id: int, cursor: int = 0, limit: int = 25) -> dict:
    limit = max(1, min(limit, MAX_PAGE))
    with store.tenant(tenant_id) as s:
        rows = s.scalars(select(Task).where(Task.status == "open", Task.id > cursor).order_by(Task.id)
                         .limit(limit)).all()
    return {"items": [{"task": t.id, "kind": t.kind, "title": t.title, "due": t.due_at.date().isoformat(),
                       "engagement": t.engagement_id, "requirement": t.requirement_id} for t in rows],
            "next_cursor": rows[-1].id if len(rows) == limit else None}


def ask(engine: Engine, tenant_id: int, question: str) -> dict:
    a = Assistant(engine).ask(tenant_id, question)
    return {"answer": a.text, "kind": a.kind, "warnings": a.warnings, "candidates": a.candidates,
            "citations": [{"document": c.document_id, "page": c.page, "quote": c.quote} for c in a.citations]}


# ---------------------------------------------------------------------------------------------- proposals

def propose(engine: Engine, tenant_id: int, agent: str, kind: str, args: dict, reason: str) -> dict:
    """Record what an agent wants changed, checked for sense first; nothing is changed."""
    summary = _summary(engine, tenant_id, kind, args)
    with engine.store.tenant(tenant_id) as s:
        p = Proposal(kind=kind, args={**args, "reason": reason}, summary=summary, proposed_by=agent,
                     expires_at=utcnow() + PROPOSAL_TTL)
        s.add(p)
        s.flush()
        s.add(AuditRow(actor=f"agent:{agent}", action="proposal_created", target=f"proposal:{p.id}",
                       detail={"kind": kind, "args": args, "reason": reason}))
        return {"proposal": p.id, "status": "proposed", "summary": summary,
                "next": f"a person confirms or declines it: testamur proposals confirm {p.id} --by NAME"}


def proposal_status(store: Store, tenant_id: int, proposal: int) -> dict:
    with store.tenant(tenant_id) as s:
        p = s.get(Proposal, proposal)
        if p is None:
            raise KeyError(f"no proposal {proposal} for this tenant")
        status = "expired" if p.status == "proposed" and p.expires_at < utcnow() else p.status
        return {"proposal": p.id, "status": status, "summary": p.summary, "decided_by": p.decided_by,
                "outcome": p.outcome}


def decide(engine: Engine, tenant_id: int, proposal: int, person: str, confirm: bool,
           now: datetime | None = None) -> str:
    """A person confirms or declines an agent's proposal. Confirmed, it is applied by the engine under the person's
    name with the agent's beside it; the agent cannot do this step."""
    now = now or utcnow()
    with engine.store.tenant(tenant_id) as s:
        p = s.get(Proposal, proposal)
        if p is None:
            raise KeyError(f"no proposal {proposal} for this tenant")
        if p.status != "proposed":
            raise ValueError(f"proposal {proposal} is already {p.status}")
        if p.expires_at < now:
            p.status = "expired"
            return "expired"
        kind, args, agent = p.kind, dict(p.args), p.proposed_by
        if not confirm:
            p.status, p.decided_by, p.decided_at, p.outcome = "declined", person, now, "declined"
            s.add(AuditRow(actor=person, action="proposal_declined", target=f"proposal:{p.id}", detail={}))
            return "declined"
    who, note = f"{person} (proposed by agent {agent})", args.get("reason", "")
    if kind == "check_verdict":
        outcome = str(engine.review_check(tenant_id, args["engagement"], args["requirement"], args["check"],
                                          args["verdict"], who, note))
    elif kind == "field_reading":
        engine.correct_field(tenant_id, args["document"], args["field"], args["value"], who, note)
        outcome = "field corrected"
    elif kind == "document_decision":
        action = args["action"]
        if action == "assign":
            engine.assign(tenant_id, args["document"], args["supplier"], who, note)
        elif action == "reject":
            engine.reject(tenant_id, args["document"], who, note)
        elif action == "clear":
            engine.clear(tenant_id, args["document"], who, note)
        else:
            engine.classify_document(tenant_id, args["document"], args["doc_type"], who, note)
        outcome = f"document {action}"
    else:
        raise ValueError(f"unknown proposal kind {kind}")
    with engine.store.tenant(tenant_id) as s:
        p = s.get(Proposal, proposal)
        p.status, p.decided_by, p.decided_at, p.outcome = "applied", person, now, outcome
        s.add(AuditRow(actor=person, action="proposal_applied", target=f"proposal:{p.id}",
                       detail={"agent": agent, "kind": kind, "outcome": outcome}))
    return outcome


def _summary(engine: Engine, tenant_id: int, kind: str, args: dict) -> str:
    """Check a proposal makes sense before it is recorded, and say in words what it would do."""
    with engine.store.tenant(tenant_id) as s:
        if kind == "check_verdict":
            if Verdict(args["verdict"]) not in (Verdict.PASS, Verdict.FAIL):
                raise ValueError("a check verdict is PASS or FAIL")
            if args["check"] not in engine.pack.requirements[args["requirement"]].checks:
                raise KeyError(f"requirement {args['requirement']} has no check {args['check']}")
            st = s.get(RequirementState, {"engagement_id": args["engagement"], "requirement_id": args["requirement"],
                                          "tenant_id": tenant_id})
            if st is None or st.document_id is None:
                raise KeyError("no document is being judged for this engagement and requirement")
            return (f"set {args['check']} to {args['verdict']} for engagement {args['engagement']} "
                    f"({args['requirement']}, document {st.document_id})")
        doc = s.get(Document, args.get("document"))
        if doc is None:
            raise KeyError(f"no document {args.get('document')} for this tenant")
        if kind == "field_reading":
            return f"read {args['field']} on document {doc.id} as {args['value']!r}"
        if kind == "document_decision":
            if args["action"] not in ("assign", "reject", "clear", "classify"):
                raise ValueError("a document decision is assign, reject, clear or classify")
            if args["action"] == "assign":
                sup = s.get(Supplier, args.get("supplier"))
                if sup is None:
                    raise KeyError(f"no supplier {args.get('supplier')} for this tenant")
                return f"assign document {doc.id} to {sup.legal_name}"
            return f"{args['action']} document {doc.id}" + (f" as {args['doc_type']}" if args.get("doc_type") else "")
    raise ValueError(f"unknown proposal kind {kind}")


def pending_proposals(store: Store, tenant_id: int) -> list[dict]:
    with store.tenant(tenant_id) as s:
        return [{"proposal": p.id, "kind": p.kind, "summary": p.summary, "by": p.proposed_by,
                 "expires": p.expires_at.isoformat(timespec="minutes")}
                for p in s.scalars(select(Proposal).where(Proposal.status == "proposed").order_by(Proposal.id))]


def count_queries(store: Store):
    """A context manager counting SQL statements, for tests of the queries per page."""
    from contextlib import contextmanager

    from sqlalchemy import event

    @contextmanager
    def counter():
        n = [0]

        def hook(*_):
            n[0] += 1
        event.listen(store.engine, "before_cursor_execute", hook)
        try:
            yield n
        finally:
            event.remove(store.engine, "before_cursor_execute", hook)
    return counter()


__all__ = ["TOOLS", "create_key", "authenticate", "grant", "revoke", "granted", "propose", "decide"]

"""Agents act for one tenant, through a key, within the tools that tenant granted, and change nothing on their own.

- **Keys.** A key names the tenant, the principal and its role; only its hash is stored. Looking a key up is the
  one read that must happen before any tenant is known, so it is done in the system session, and nothing else is.
  Only an agent's key opens the agent surface; only a person's opens the service (testamur.service), and only a
  reviewer's can approve.
- **Catalogue.** A tenant grants and revokes tools by name. A tool not granted is not listed and is refused if
  called, checked on every call, so a revocation takes effect immediately.
- **Reads** paginate with a cursor and run a fixed number of queries per page, however many rows a tenant has: an
  agent can call a tool far more often than a person clicks, so a query per row would not survive.
- **Writes are proposals.** An agent proposes a decision (a check verdict, a field reading, a document decision); a
  person confirms or declines it. A proposal is bound to the evidence it was made on; confirming it applies it in one
  transaction with its own state change, audit and outbox event, through the same engine call a person would make,
  under the person's name with the agent's recorded beside it. A proposal whose evidence changed is refused as stale.
  Proposals expire after a week.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select

from .assistant import Assistant
from .engine import Engine
from .model import Verdict
from .store import (
    ApiKey,
    AuditRow,
    Claim,
    Creditor,
    Document,
    DocumentReview,
    FieldCorrection,
    Hold,
    OutboxEvent,
    Proposal,
    RequirementState,
    Store,
    Task,
    ToolGrant,
    utcnow,
)
from .telemetry import traced

READ_TOOLS = ("list_claims", "get_requirement", "review_queue", "open_tasks", "ask", "proposal_status")
PROPOSE_TOOLS = ("propose_check_verdict", "propose_field_reading", "propose_document_decision")
TOOLS = READ_TOOLS + PROPOSE_TOOLS
ROLES = ("agent", "uploader", "reviewer")
PROPOSAL_TTL = timedelta(days=7)
MAX_PAGE = 100


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


# ---------------------------------------------------------------------------------------------- keys and catalogue

def create_key(store: Store, tenant_id: int, name: str, role: str = "agent") -> str:
    """A new key for a principal of this tenant. The key is shown once; only its hash is kept."""
    if role not in ROLES:
        raise ValueError(f"role is one of {', '.join(ROLES)}")
    key = "tsk_" + secrets.token_urlsafe(24)
    with store.tenant(tenant_id) as s:
        s.add(ApiKey(name=name, key_hash=_hash(key), role=role))
        s.add(AuditRow(actor="admin", action="agent_key_created" if role == "agent" else "key_created",
                       target=f"{role}:{name}", detail={"role": role}))
    return key


@dataclass(frozen=True)
class Principal:
    tenant_id: int
    name: str
    role: str
    key_hash: str


def principal_of(store: Store, key: str | None = None, key_hash: str | None = None) -> Principal:
    """Who a key (or, from a signed session, the hash of one) stands for, while it is live. The only cross-tenant
    read: the key decides the tenant. Called on every request, so a revocation takes effect on the next one."""
    digest = key_hash if key_hash is not None else _hash(key or "")
    with store.system() as s:
        row = s.scalar(select(ApiKey).where(ApiKey.key_hash == digest, ApiKey.revoked_at.is_(None)))
        if row is None:
            raise PermissionError("unknown or revoked key")
        return Principal(row.tenant_id, row.name, row.role, row.key_hash)


def authenticate(store: Store, key: str, role: str = "agent") -> tuple[int, str]:
    """The tenant and name an agent's key stands for: a person's key does not open the agent surface, so nothing an
    agent did is recorded under a person's name, nor the reverse."""
    who = principal_of(store, key)
    if who.role != role:
        raise PermissionError(f"a {who.role}'s key does not open this surface")
    return who.tenant_id, who.name


def revoke_key(store: Store, tenant_id: int, name: str, by: str) -> int:
    """Revoke every live key of the named agent. Takes effect on the next call, on servers already running too."""
    with store.tenant(tenant_id) as s:
        rows = s.scalars(select(ApiKey).where(ApiKey.name == name, ApiKey.revoked_at.is_(None))).all()
        for row in rows:
            row.revoked_at = utcnow()
        s.add(AuditRow(actor=by, action="agent_key_revoked", target=f"agent:{name}", detail={"keys": len(rows)}))
        return len(rows)


def grant(store: Store, tenant_id: int, tool: str, by: str, principal: str = "*") -> None:
    """Grant a tool to every agent of the tenant ("*") or to one agent by name."""
    if tool not in TOOLS:
        raise KeyError(f"unknown tool {tool}")
    with store.tenant(tenant_id) as s:
        row = s.scalar(select(ToolGrant).where(ToolGrant.tool == tool, ToolGrant.principal == principal))
        if row is None:
            s.add(ToolGrant(tool=tool, principal=principal, granted_by=by))
        else:
            row.revoked_at, row.granted_by, row.granted_at = None, by, utcnow()
        s.add(AuditRow(actor=by, action="tool_granted", target=f"tool:{tool}", detail={"principal": principal}))


def revoke(store: Store, tenant_id: int, tool: str, by: str, principal: str = "*") -> None:
    with store.tenant(tenant_id) as s:
        row = s.scalar(select(ToolGrant).where(ToolGrant.tool == tool, ToolGrant.principal == principal,
                                               ToolGrant.revoked_at.is_(None)))
        if row is not None:
            row.revoked_at = utcnow()
            s.add(AuditRow(actor=by, action="tool_revoked", target=f"tool:{tool}", detail={"principal": principal}))


def granted(store: Store, tenant_id: int, principal: str | None = None) -> set[str]:
    """The tools an agent may call: those granted to every agent of the tenant, and those granted to it by name."""
    whom = ["*"] if principal is None else ["*", principal]
    with store.tenant(tenant_id) as s:
        return {g.tool for g in s.scalars(select(ToolGrant).where(ToolGrant.revoked_at.is_(None),
                                                                  ToolGrant.principal.in_(whom)))}


def require(store: Store, tenant_id: int, tool: str, principal: str | None = None) -> None:
    if tool not in granted(store, tenant_id, principal):
        raise PermissionError(f"the tool {tool} is not granted to this agent")


def require_key(store: Store, api_key: str, tenant_id: int, agent: str, tool: str) -> None:
    """Every call, not once per connection: the key must still be live and still be the same agent's, and the tool
    still granted to it. A revoked key or tool stops working at once, on a server built before the revocation."""
    if authenticate(store, api_key) != (tenant_id, agent):
        raise PermissionError("the key no longer stands for this agent")
    require(store, tenant_id, tool, agent)


# ---------------------------------------------------------------------------------------------- reads

def list_claims(store: Store, tenant_id: int, cursor: int = 0, limit: int = 25) -> dict:
    """A page of claims with each requirement's status and any hold. Four queries, whatever the
    page size: claims, their creditors, their statuses, their holds."""
    limit = max(1, min(limit, MAX_PAGE))
    with store.tenant(tenant_id) as s:
        engs = s.scalars(select(Claim).where(Claim.id > cursor).order_by(Claim.id).limit(limit)).all()
        ids = [e.id for e in engs]
        names = {x.id: x.legal_name for x in s.scalars(select(Creditor).where(
            Creditor.id.in_({e.creditor_id for e in engs})))}
        states: dict[int, dict] = defaultdict(dict)
        for st in s.scalars(select(RequirementState).where(RequirementState.claim_id.in_(ids))):
            states[st.claim_id][st.requirement_id] = st.status
        held: dict[int, list[str]] = defaultdict(list)
        for h in s.scalars(select(Hold).where(Hold.claim_id.in_(ids), Hold.released_at.is_(None))
                           .order_by(Hold.action)):
            held[h.claim_id].append(h.action)
    items = [{"claim": e.id, "creditor": names.get(e.creditor_id), "tier": e.tier, "type": e.claim_type,
              "statuses": states.get(e.id, {}), "on_hold": bool(held.get(e.id)), "held_for": held.get(e.id, [])}
             for e in engs]
    return {"items": items, "next_cursor": ids[-1] if len(ids) == limit else None}


def get_requirement(engine: Engine, tenant_id: int, claim: int, requirement: str) -> dict:
    with engine.store.tenant(tenant_id) as s:
        st, results = engine._verdicts(s, tenant_id, claim, requirement)
    if st is None:
        raise KeyError("no such claim and requirement for this tenant")
    return {"claim": claim, "requirement": requirement, "status": st.status, "document": st.document_id,
            "reasons": st.reasons,
            "checks": [{"check": r.check_id, "verdict": r.verdict.value, "reason": r.reason,
                        "evidence": [{"page": e.page, "quote": e.quote} for e in r.evidence if e.quote],
                        "fix": r.fix_hint} for r in results]}


def review_queue(engine: Engine, tenant_id: int, cursor: int = 0, limit: int = 25) -> dict:
    items = engine.queue(tenant_id)
    limit = max(1, min(limit, MAX_PAGE))
    page = items[cursor:cursor + limit]
    return {"items": [{"kind": i.kind, "document": i.document_id, "claim": i.claim_id,
                       "requirement": i.requirement_id, "check": i.check_id, "reason": i.reason,
                       "blocking": i.blocking} for i in page],
            "next_cursor": cursor + limit if cursor + limit < len(items) else None, "total": len(items)}


def open_tasks(store: Store, tenant_id: int, cursor: int = 0, limit: int = 25) -> dict:
    limit = max(1, min(limit, MAX_PAGE))
    with store.tenant(tenant_id) as s:
        rows = s.scalars(select(Task).where(Task.status == "open", Task.id > cursor).order_by(Task.id)
                         .limit(limit)).all()
    return {"items": [{"task": t.id, "kind": t.kind, "title": t.title, "due": t.due_at.date().isoformat(),
                       "claim": t.claim_id, "requirement": t.requirement_id} for t in rows],
            "next_cursor": rows[-1].id if len(rows) == limit else None}


def ask(engine: Engine, tenant_id: int, question: str) -> dict:
    a = Assistant(engine).ask(tenant_id, question)
    return {"answer": a.text, "kind": a.kind, "warnings": a.warnings, "candidates": a.candidates,
            "citations": [{"document": c.document_id, "page": c.page, "quote": c.quote} for c in a.citations]}


# ---------------------------------------------------------------------------------------------- proposals

class StaleProposal(ValueError):
    """The proposal is not the one the person was shown, or the evidence it was made on has changed."""


@traced("proposal", before=lambda engine, tenant_id, agent, kind, *a, **k: {"tenant_id": tenant_id, "kind": kind},
        after=lambda r: {"proposal_id": r["proposal"]})
def propose(engine: Engine, tenant_id: int, agent: str, kind: str, args: dict, reason: str,
            origin: str | None = None) -> dict:
    """Record what an agent wants changed, checked for sense first and bound to the evidence it was made on; nothing
    is changed. `origin` names the workflow step that made it (unique per tenant), so a step that runs again finds its
    proposal instead of making a second one."""
    with engine.store.tenant(tenant_id) as s:
        binding, summary = _binding(engine, s, kind, args)
        full = {**args, "reason": reason}
        p = Proposal(kind=kind, args=full, binding=binding, binding_digest=_digest(kind, full, binding),
                     summary=summary, proposed_by=agent, expires_at=utcnow() + PROPOSAL_TTL, origin=origin)
        s.add(p)
        s.flush()
        s.add(AuditRow(actor=f"agent:{agent}", action="proposal_created", target=f"proposal:{p.id}",
                       detail={"kind": kind, "args": args, "reason": reason, "binding": binding}))
        return {"proposal": p.id, "status": "proposed", "summary": summary, "digest": p.binding_digest,
                "next": f"a person confirms or declines it: testamur proposals confirm {p.id} --by NAME"}


def proposal_status(store: Store, tenant_id: int, proposal: int) -> dict:
    with store.tenant(tenant_id) as s:
        p = s.get(Proposal, proposal)
        if p is None:
            raise KeyError(f"no proposal {proposal} for this tenant")
        status = "expired" if p.status == "proposed" and p.expires_at < utcnow() else p.status
        return {"proposal": p.id, "status": status, "summary": p.summary, "digest": p.binding_digest,
                "binding": p.binding, "decided_by": p.decided_by, "outcome": p.outcome}


@traced("approval", before=lambda engine, tenant_id, proposal, *a, **k: {"tenant_id": tenant_id,
                                                                          "proposal_id": proposal},
        after=lambda outcome: {"outcome": outcome})
def decide(engine: Engine, tenant_id: int, proposal: int, person: str, confirm: bool,
           now: datetime | None = None, seen: str | None = None, fail_at: str | None = None) -> str:
    """A person confirms or declines an agent's proposal; the agent cannot do this step.

    One transaction does all of it: the proposal is claimed (locked on Postgres; SQLite transactions are serialised),
    its evidence is derived again and compared with what it was bound to, the change is applied by the engine under the
    person's name with the agent's beside it, the proposal is marked, audited and announced through the outbox. A crash
    anywhere before the commit leaves nothing; after it, everything, and a second confirmation is refused. The effect
    carries the proposal's id under a unique key, so it cannot exist twice whatever happens to the proposal row.

    `seen` is the binding digest the person was shown: a different one is refused. A proposal whose evidence changed
    since it was made (a newer document, a correction, a changed rule, another engine) is marked stale and nothing is
    applied. `fail_at` crashes on purpose at a step, for tests."""
    now = now or utcnow()
    with engine.store.tenant(tenant_id) as s:
        claim = select(Proposal).where(Proposal.id == proposal)
        p = s.scalar(claim.with_for_update() if engine.store.dialect == "postgresql" else claim)
        if p is None:
            raise KeyError(f"no proposal {proposal} for this tenant")
        if p.status != "proposed":
            raise ValueError(f"proposal {proposal} is already {p.status}")
        if seen is not None and seen != p.binding_digest:
            raise StaleProposal(f"proposal {proposal} is not the one you were shown")
        if p.expires_at < now:
            p.status, p.decided_at, p.outcome = "expired", now, "expired before anyone decided"
            s.add(AuditRow(actor=person, action="proposal_expired", target=f"proposal:{p.id}", detail={}))
            return "expired"
        if not confirm:
            p.status, p.decided_by, p.decided_at, p.outcome = "declined", person, now, "declined"
            s.add(AuditRow(actor=person, action="proposal_declined", target=f"proposal:{p.id}", detail={}))
            return "declined"
        args = {k: v for k, v in p.args.items() if k != "reason"}
        try:
            current, _ = _binding(engine, s, p.kind, args)
        except (KeyError, ValueError):
            current = None
        if current != p.binding:
            p.status, p.decided_by, p.decided_at = "stale", person, now
            p.outcome = "the evidence changed after the proposal was made; nothing was applied: propose again"
            s.add(AuditRow(actor=person, action="proposal_stale", target=f"proposal:{p.id}",
                           detail={"bound": p.binding, "now": current}))
            return "stale"
        who = f"{person} (proposed by agent {p.proposed_by})"
        outcome = _apply(engine, s, tenant_id, p, who, p.args.get("reason", ""))
        if fail_at == "after_effect":
            raise RuntimeError("crashed after the effect, before the proposal was marked")
        p.status, p.decided_by, p.decided_at, p.outcome = "applied", person, now, outcome
        s.add(AuditRow(actor=person, action="proposal_applied", target=f"proposal:{p.id}",
                       detail={"agent": p.proposed_by, "kind": p.kind, "outcome": outcome,
                               "digest": p.binding_digest}))
        s.add(OutboxEvent(key=f"proposal:{p.id}", kind="proposal.applied",
                          payload={"proposal": p.id, "kind": p.kind, "by": person, "agent": p.proposed_by,
                                   "outcome": outcome, "digest": p.binding_digest}))
        if fail_at == "before_commit":
            raise RuntimeError("crashed before the commit")
    if fail_at == "after_commit":
        raise RuntimeError("crashed after the commit")
    return outcome


def _apply(engine: Engine, s, tenant_id: int, p: Proposal, who: str, note: str) -> str:
    args = p.args
    if p.kind == "check_verdict":
        return str(engine.review_check_in(s, tenant_id, args["claim"], args["requirement"], args["check"],
                                          args["verdict"], who, note, proposal_id=p.id,
                                          document_id=p.binding["document"]))
    if p.kind == "field_reading":
        engine.correct_field_in(s, tenant_id, args["document"], args["field"], args["value"], who, note,
                                proposal_id=p.id)
        return "field corrected"
    if p.kind == "document_decision":
        engine.document_decision_in(s, tenant_id, args["document"], args["action"], who, note,
                                    doc_type=args.get("doc_type"), creditor_id=args.get("creditor"), proposal_id=p.id)
        return f"document {args['action']}"
    raise ValueError(f"unknown proposal kind {p.kind}")


def _digest(kind: str, args: dict, binding: dict) -> str:
    raw = json.dumps({"kind": kind, "args": args, "binding": binding}, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _binding(engine: Engine, s, kind: str, args: dict) -> tuple[dict, str]:
    """What a proposal is about, as it stands now (the evidence a person is shown and confirms), and what it would do
    in words. Raises if it makes no sense."""
    if kind == "check_verdict":
        if Verdict(args["verdict"]) not in (Verdict.PASS, Verdict.FAIL):
            raise ValueError("a check verdict is PASS or FAIL")
        if args["check"] not in engine.pack.requirements[args["requirement"]].checks:
            raise KeyError(f"requirement {args['requirement']} has no check {args['check']}")
        st = s.get(RequirementState, {"claim_id": args["claim"], "requirement_id": args["requirement"],
                                      "tenant_id": s.info["tenant_id"]})
        if st is None or st.document_id is None:
            raise KeyError("no document is being judged for this claim and requirement")
        doc, e = s.get(Document, st.document_id), s.get(Claim, args["claim"])
        binding = {"claim": e.id, "matter": e.matter_id, "requirement": args["requirement"], "check": args["check"],
                   "document": doc.id, "sha256": doc.sha256, "evidence": st.evidence_digest,
                   "rule": engine.pack.rule_digest(args["check"]), "engine": engine.version}
        return binding, (f"set {args['check']} to {args['verdict']} for claim {args['claim']} "
                         f"({args['requirement']}, document {doc.id})")
    doc = s.get(Document, args.get("document"))
    if doc is None:
        raise KeyError(f"no document {args.get('document')} for this tenant")
    base = {"document": doc.id, "matter": doc.matter_id, "sha256": doc.sha256, "engine": doc.engine_version}
    if kind == "field_reading":
        if doc.doc_type is None or args["field"] not in engine.pack.doc_types[doc.doc_type].fields:
            raise KeyError(f"document {doc.id} has no field {args['field']}")
        corrections = [c.id for c in s.scalars(select(FieldCorrection).where(
            FieldCorrection.document_id == doc.id, FieldCorrection.field == args["field"]).order_by(FieldCorrection.id))]
        binding = {**base, "field": args["field"], "reading": (doc.fields or {}).get(args["field"]),
                   "conflicts": (doc.conflicts or {}).get(args["field"]), "corrections": corrections}
        return binding, f"read {args['field']} on document {doc.id} as {args['value']!r}"
    if kind == "document_decision":
        if args["action"] not in ("assign", "reject", "clear", "classify"):
            raise ValueError("a document decision is assign, reject, clear or classify")
        decisions = [d.id for d in s.scalars(select(DocumentReview).where(DocumentReview.document_id == doc.id)
                                             .order_by(DocumentReview.id))]
        binding = {**base, "state": doc.state, "doc_type": doc.doc_type, "creditor": doc.creditor_id,
                   "hostile": doc.hostile, "cleared": doc.cleared, "decisions": decisions}
        if args["action"] == "assign":
            sup = s.get(Creditor, args.get("creditor"))
            if sup is None:
                raise KeyError(f"no creditor {args.get('creditor')} for this tenant")
            return binding, f"assign document {doc.id} to {sup.legal_name}"
        return binding, f"{args['action']} document {doc.id}" + (f" as {args['doc_type']}" if args.get("doc_type")
                                                                  else "")
    raise ValueError(f"unknown proposal kind {kind}")


def pending_proposals(store: Store, tenant_id: int) -> list[dict]:
    with store.tenant(tenant_id) as s:
        return [{"proposal": p.id, "kind": p.kind, "summary": p.summary, "by": p.proposed_by,
                 "digest": p.binding_digest, "expires": p.expires_at.isoformat(timespec="minutes")}
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


__all__ = ["TOOLS", "ROLES", "Principal", "create_key", "principal_of", "authenticate", "grant", "revoke", "granted",
           "propose", "decide"]

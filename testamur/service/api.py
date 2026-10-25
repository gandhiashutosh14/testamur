"""The JSON API, under /api/v1. Every route acts for the tenant of the caller's key: no route takes a tenant from the
path or the body. Slow work is queued (an upload's reading, an investigation) and followed by polling; a person's
decision is applied in the request, atomically, against the evidence it was shown."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import agents, jobs
from ..agents import Principal, StaleProposal
from ..store import (
    AuditRow,
    Blob,
    Claim,
    ClaimPermission,
    Creditor,
    Document,
    Hold,
    InvestigationRun,
    Matter,
    Proposal,
    RequirementState,
    UploadRow,
    utcnow,
)
from .auth import requires

router = APIRouter(prefix="/api/v1")
reviewer = requires("reviewer")
uploader = requires("uploader", "reviewer")


def _rt(request: Request):
    return request.app.state.runtime


# ---------------------------------------------------------------------------------------------- shapes

class Me(BaseModel):
    tenant: int
    name: str
    role: str


class Queued(BaseModel):
    status: Literal["queued"] = "queued"
    job: str


class UploadQueued(Queued):
    upload: int


class RunQueued(Queued):
    run: int


class InvestigationRequest(BaseModel):
    mode: Literal["pair", "single"] = "pair"


class Decision(BaseModel):
    proposal: int
    approve: bool
    digest: str = Field(min_length=16, max_length=128, description="the binding digest the person was shown")


class Decisions(BaseModel):
    decisions: list[Decision] = Field(min_length=1, max_length=50)


class ProposalDecision(BaseModel):
    approve: bool
    digest: str = Field(min_length=16, max_length=128)


class Question(BaseModel):
    question: str = Field(min_length=3, max_length=500)


# ---------------------------------------------------------------------------------------------- who

@router.get("/me", response_model=Me)
def me(who: Principal = Depends(uploader)) -> Me:
    return Me(tenant=who.tenant_id, name=who.name, role=who.role)


# ---------------------------------------------------------------------------------------------- intake

@router.post("/matters/{matter_id}/uploads", status_code=202, response_model=UploadQueued)
def upload(request: Request, matter_id: int, file: UploadFile = File(...),
           declared_creditor_id: int | None = Form(None), who: Principal = Depends(uploader)) -> UploadQueued:
    """Store a document and queue its reading; follow it at /uploads/{upload}."""
    rt = _rt(request)
    limit = rt.settings.max_upload_bytes
    data = file.file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"a document may be at most {limit} bytes")
    if not data:
        raise HTTPException(422, "the file is empty")
    with rt.store.tenant(who.tenant_id) as s:
        if s.get(Matter, matter_id) is None:
            raise HTTPException(404, f"no process {matter_id} for this firm")
        if declared_creditor_id is not None and s.get(Creditor, declared_creditor_id) is None:
            raise HTTPException(404, f"no creditor {declared_creditor_id} for this firm")
    name = (file.filename or "upload")[-120:]
    upload_id = rt.engine.ingest(who.tenant_id, matter_id, name, data, "service", utcnow(), declared_creditor_id)
    with rt.store.tenant(who.tenant_id) as s:
        s.add(AuditRow(actor=who.name, action="uploaded", target=f"upload:{upload_id}",
                       detail={"matter": matter_id, "bytes": len(data)}))
    key = f"upload:{upload_id}"
    jobs.enqueue(rt.store, who.tenant_id, "process_upload", key, {"upload_id": upload_id})
    return UploadQueued(upload=upload_id, job=key)


@router.get("/uploads/{upload_id}")
def upload_status(request: Request, upload_id: int, who: Principal = Depends(uploader)) -> dict:
    rt = _rt(request)
    with rt.store.tenant(who.tenant_id) as s:
        up = s.get(UploadRow, upload_id)
        if up is None:
            raise HTTPException(404, f"no upload {upload_id} for this firm")
        docs = [{"document": d.id, "type": d.doc_type, "state": d.state, "creditor": d.creditor_id,
                 "quarantined": bool(d.hostile and not d.cleared)}
                for d in s.scalars(select(Document).where(Document.sha256 == up.sha256,
                                                          Document.matter_id == up.matter_id))]
        out = {"upload": up.id, "matter": up.matter_id, "file_name": up.filename,
               "uploaded_at": up.uploaded_at.isoformat(timespec="seconds"), "documents": docs}
    with rt.store.system() as s:               # the queue is shared infrastructure: read by the tenant's own key
        job = s.scalar(select(jobs.Job).where(jobs.Job.tenant_id == who.tenant_id,
                                              jobs.Job.key == f"upload:{upload_id}"))
        out["job"] = None if job is None else {"status": job.status, "attempts": job.attempts,
                                               "error": (job.last_error or None) and job.last_error[:300]}
    return out


# ---------------------------------------------------------------------------------------------- claims and evidence

@router.get("/claims")
def claims(request: Request, cursor: int = Query(0, ge=0), limit: int = Query(25, ge=1, le=100),
           who: Principal = Depends(reviewer)) -> dict:
    return agents.list_claims(_rt(request).store, who.tenant_id, cursor, limit)


@router.get("/claims/{claim_id}")
def claim(request: Request, claim_id: int, who: Principal = Depends(reviewer)) -> dict:
    return claim_view(_rt(request), who.tenant_id, claim_id)


def claim_view(rt, tenant_id: int, claim_id: int) -> dict:
    with rt.store.tenant(tenant_id) as s:
        c = s.get(Claim, claim_id)
        if c is None:
            raise HTTPException(404, f"no claim {claim_id} for this firm")
        creditor, m = s.get(Creditor, c.creditor_id), s.get(Matter, c.matter_id)
        states = s.scalars(select(RequirementState).where(RequirementState.claim_id == claim_id)
                           .order_by(RequirementState.requirement_id)).all()
        holds = s.scalars(select(Hold).where(Hold.claim_id == claim_id, Hold.released_at.is_(None))).all()
        perms = s.scalars(select(ClaimPermission).where(ClaimPermission.claim_id == claim_id)).all()
        runs = s.scalars(select(InvestigationRun).where(InvestigationRun.claim_id == claim_id)
                         .order_by(InvestigationRun.id.desc()).limit(10)).all()
        return {"claim": c.id, "creditor": creditor.legal_name, "process": m.name, "matter": m.id,
                "tier": c.tier, "type": c.claim_type, "facility": c.facility_type,
                "requirements": [{"requirement": st.requirement_id, "status": st.status, "document": st.document_id,
                                  "reasons": list(st.reasons or [])[:6]} for st in states],
                "holds": [{"action": h.action, "reasons": list(h.reasons or [])[:4]} for h in holds],
                "permissions": [{"action": p.action, "granted": p.granted, "unmet": list(p.unmet or [])[:4]}
                                for p in perms],
                "investigations": [{"run": r.id, "mode": r.mode, "status": r.status,
                                    "at": r.created_at.isoformat(timespec="minutes")} for r in runs]}


@router.get("/claims/{claim_id}/requirements/{requirement}")
def requirement(request: Request, claim_id: int, requirement: str, who: Principal = Depends(reviewer)) -> dict:
    try:
        return agents.get_requirement(_rt(request).engine, who.tenant_id, claim_id, requirement)
    except KeyError:
        raise HTTPException(404, "no such claim and requirement for this firm") from None


@router.get("/documents/{document_id}")
def document(request: Request, document_id: int, who: Principal = Depends(reviewer)) -> dict:
    rt = _rt(request)
    with rt.store.tenant(who.tenant_id) as s:
        d = _doc(s, document_id)
        pages = rt.engine._extraction(s, who.tenant_id, d.sha256).pages
        return {"document": d.id, "type": d.doc_type, "state": d.state, "matter": d.matter_id,
                "creditor": d.creditor_id, "pages": len(pages), "quarantined": bool(d.hostile and not d.cleared)}


@router.get("/documents/{document_id}/pages/{page}")
def page(request: Request, document_id: int, page: int, who: Principal = Depends(reviewer)) -> dict:
    return page_view(_rt(request), who.tenant_id, document_id, page)


def page_view(rt, tenant_id: int, document_id: int, page: int) -> dict:
    """A page's lines, for a person. A quarantined document is shown to a reviewer (who decides whether to clear it),
    never to an agent."""
    with rt.store.tenant(tenant_id) as s:
        d = _doc(s, document_id)
        pages = rt.engine._extraction(s, tenant_id, d.sha256).pages
        p = next((x for x in pages if x.number == page), None)
        if p is None:
            raise HTTPException(404, f"document {document_id} has {len(pages)} page(s)")
        return {"document": d.id, "type": d.doc_type, "page": page, "of": len(pages),
                "quarantined": bool(d.hostile and not d.cleared), "lines": [line.text for line in p.lines]}


@router.post("/documents/{document_id}/ask")
def ask_document(request: Request, document_id: int, body: Question, who: Principal = Depends(reviewer)) -> dict:
    """Ask the document a question: an answer with quotes checked to be on their pages, or an abstention. The
    pipeline the evidence evaluation measures (reports/evidence-eval.md), on this document's pages."""
    return ask_view(_rt(request), who.tenant_id, document_id, body.question)


def ask_view(rt, tenant_id: int, document_id: int, question: str) -> dict:
    from ..evidence.ask import ask_pages
    from ..telemetry import span
    if rt.engine.model is None:
        raise HTTPException(503, "no model is configured for this service (TESTAMUR_MODEL)")
    with rt.store.tenant(tenant_id) as s:
        d = _doc(s, document_id)
        if d.hostile and not d.cleared:
            raise HTTPException(409, "this document carries text aimed at automated readers: no model is shown it "
                                     "until a person has cleared it")
        pages = rt.engine._extraction(s, tenant_id, d.sha256).pages
    with span("ask", tenant_id=tenant_id, document_id=document_id) as note:
        out = ask_pages(rt.engine.model, question, pages)
        note(outcome="abstained" if out["abstained"] else "answered", count=len(out["citations"]))
    return {"document": document_id, "question": question, **out}


@router.get("/documents/{document_id}/file")
def document_file(request: Request, document_id: int, who: Principal = Depends(reviewer)) -> Response:
    return file_response(_rt(request), who.tenant_id, document_id)


def file_response(rt, tenant_id: int, document_id: int) -> Response:
    """The document's bytes, always as a download: a file a creditor sent is never rendered in the service's
    origin."""
    with rt.store.tenant(tenant_id) as s:
        d = _doc(s, document_id)
        blob = s.get(Blob, {"sha256": d.sha256, "tenant_id": tenant_id})
        mime, where = blob.mime, blob.path
    return Response(rt.engine.blobs.get(where), media_type=mime,
                    headers={"Content-Disposition": f'attachment; filename="document-{document_id}"'})


def _doc(s, document_id: int) -> Document:
    d = s.get(Document, document_id)
    if d is None:
        raise HTTPException(404, f"no document {document_id} for this firm")
    return d


# ---------------------------------------------------------------------------------------------- investigations

@router.post("/claims/{claim_id}/investigations", status_code=202, response_model=RunQueued)
def investigate(request: Request, claim_id: int, body: InvestigationRequest | None = None,
                who: Principal = Depends(reviewer)) -> RunQueued:
    """Queue an investigation of the claim by the investigator and verifier agents; follow it at
    /investigations/{run}."""
    return start_run(_rt(request), who, claim_id, (body or InvestigationRequest()).mode)


def start_run(rt, who: Principal, claim_id: int, mode: str) -> RunQueued:
    try:
        run_id = rt.investigations.create(who.tenant_id, claim_id, who.name, mode)
    except KeyError:
        raise HTTPException(404, f"no claim {claim_id} for this firm") from None
    key = f"run:{run_id}"
    jobs.enqueue(rt.store, who.tenant_id, "investigate", key, {"run_id": run_id}, max_attempts=3)
    return RunQueued(run=run_id, job=key)


@router.get("/investigations")
def investigations(request: Request, status: str | None = None, who: Principal = Depends(reviewer)) -> dict:
    with _rt(request).store.tenant(who.tenant_id) as s:
        q = select(InvestigationRun).order_by(InvestigationRun.id.desc()).limit(50)
        if status:
            q = q.where(InvestigationRun.status == status)
        return {"items": [{"run": r.id, "claim": r.claim_id, "mode": r.mode, "status": r.status,
                           "requested_by": r.requested_by, "at": r.created_at.isoformat(timespec="minutes")}
                          for r in s.scalars(q)]}


@router.get("/investigations/{run_id}")
def investigation(request: Request, run_id: int, who: Principal = Depends(reviewer)) -> dict:
    try:
        return _rt(request).investigations.status(who.tenant_id, run_id)
    except KeyError:
        raise HTTPException(404, f"no investigation {run_id} for this firm") from None


@router.post("/investigations/{run_id}/cancel")
def cancel(request: Request, run_id: int, who: Principal = Depends(reviewer)) -> dict:
    try:
        return _rt(request).investigations.cancel(who.tenant_id, run_id, who.name)
    except KeyError:
        raise HTTPException(404, f"no investigation {run_id} for this firm") from None


@router.post("/investigations/{run_id}/decisions")
def decide_run(request: Request, run_id: int, body: Decisions, who: Principal = Depends(reviewer)) -> dict:
    """A reviewer's decisions on a run's proposals, each against the digest it was shown. Applied atomically, under the
    reviewer's name; a proposal whose evidence moved since is refused as stale and nothing of it is applied."""
    return decide_on_run(_rt(request), who, run_id, [d.model_dump() for d in body.decisions])


def decide_on_run(rt, who: Principal, run_id: int, decisions: list[dict]) -> dict:
    try:
        return rt.investigations.resume(who.tenant_id, run_id, decisions, by=who.name)
    except KeyError:
        raise HTTPException(404, f"no investigation {run_id} for this firm") from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


# ---------------------------------------------------------------------------------------------- proposals

@router.get("/proposals")
def proposals(request: Request, who: Principal = Depends(reviewer)) -> dict:
    return {"items": agents.pending_proposals(_rt(request).store, who.tenant_id)}


@router.post("/proposals/{proposal_id}/decision")
def decide(request: Request, proposal_id: int, body: ProposalDecision, who: Principal = Depends(reviewer)) -> dict:
    """Decide an agent's proposal made through the agent surface. A proposal an investigation made is decided on its
    run, which records the outcome there."""
    rt = _rt(request)
    with rt.store.tenant(who.tenant_id) as s:
        p = s.get(Proposal, proposal_id)
        if p is None:
            raise HTTPException(404, f"no proposal {proposal_id} for this firm")
        if (p.origin or "").startswith("run:"):
            raise HTTPException(409, f"proposal {proposal_id} belongs to investigation {p.origin.split(':')[1]}: "
                                     "decide it there")
    try:
        outcome = agents.decide(rt.engine, who.tenant_id, proposal_id, who.name, body.approve, seen=body.digest)
    except StaleProposal as exc:
        raise HTTPException(409, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    return {"proposal": proposal_id, "outcome": outcome}

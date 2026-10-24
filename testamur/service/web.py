"""The review page: server-rendered, for a reviewer signed in with their key. It reads through the same functions as
the API and decides through the same atomic, digest-checked calls; every form carries the session's CSRF token."""
from __future__ import annotations

import re
import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from .. import agents
from ..agents import Principal
from ..investigate import pending_runs
from ..store import InvestigationRun
from .api import claim_view, decide_on_run, page_view, start_run
from .auth import check_csrf, csrf_token, session_reviewer, sign_in

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
router = APIRouter(include_in_schema=False)
WORKING = {"started", "investigating", "evidence_ready", "verifying"}


def _page(request: Request, name: str, **ctx) -> HTMLResponse:
    who = getattr(request.state, "principal", None)
    return templates.TemplateResponse(request, name, {"who": who, "csrf": csrf_token(request), **ctx})


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    # a token before anyone signs in, so another site cannot sign a reviewer's browser in as someone else
    request.session.setdefault("csrf", secrets.token_urlsafe(24))
    return _page(request, "login.html", error=None)


@router.post("/login")
def login(request: Request, key: str = Form(...), csrf: str = Form("")):
    check_csrf(request, csrf)
    try:
        sign_in(request, key.strip())
    except PermissionError as exc:
        return _page(request, "login.html", error=str(exc) if "reviewer" in str(exc) else "unknown or revoked key")
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
def logout(request: Request, csrf: str = Form("")):
    check_csrf(request, csrf)
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.get("/", response_class=HTMLResponse)
def home(request: Request, who: Principal = Depends(session_reviewer)):
    rt = request.app.state.runtime
    with rt.store.tenant(who.tenant_id) as s:
        runs = s.scalars(select(InvestigationRun).order_by(InvestigationRun.id.desc()).limit(12)).all()
        recent = [{"run": r.id, "claim": r.claim_id, "mode": r.mode, "status": r.status, "by": r.requested_by,
                   "at": r.created_at.isoformat(timespec="minutes")} for r in runs]
    return _page(request, "home.html", waiting=pending_runs(rt.engine, who.tenant_id), recent=recent,
                 claims=agents.list_claims(rt.store, who.tenant_id, 0, 50)["items"],
                 queue=agents.review_queue(rt.engine, who.tenant_id, 0, 10))


@router.get("/claims/{claim_id}", response_class=HTMLResponse)
def claim_page(request: Request, claim_id: int, who: Principal = Depends(session_reviewer)):
    rt = request.app.state.runtime
    view = claim_view(rt, who.tenant_id, claim_id)
    checks = {}
    for r in view["requirements"]:
        try:
            checks[r["requirement"]] = agents.get_requirement(rt.engine, who.tenant_id, claim_id,
                                                              r["requirement"])["checks"]
        except KeyError:
            checks[r["requirement"]] = []
    return _page(request, "claim.html", c=view, checks=checks)


@router.post("/claims/{claim_id}/investigate")
def investigate(request: Request, claim_id: int, mode: str = Form("pair"), csrf: str = Form(""),
                who: Principal = Depends(session_reviewer)):
    check_csrf(request, csrf)
    if mode not in ("pair", "single"):
        raise HTTPException(422, "mode is pair or single")
    queued = start_run(request.app.state.runtime, who, claim_id, mode)
    return RedirectResponse(f"/runs/{queued.run}", status_code=303)


@router.get("/runs/{run_id}", response_class=HTMLResponse)
def run_page(request: Request, run_id: int, who: Principal = Depends(session_reviewer)):
    try:
        run = request.app.state.runtime.investigations.status(who.tenant_id, run_id)
    except KeyError:
        raise HTTPException(404, f"no investigation {run_id} for this firm") from None
    return _page(request, "run.html", run=run, working=run["status"] in WORKING)


@router.post("/runs/{run_id}/decide")
async def decide(request: Request, run_id: int, who: Principal = Depends(session_reviewer)):
    form = await request.form()
    check_csrf(request, str(form.get("csrf", "")))
    decisions = []
    for name, value in form.multi_items():
        m = re.fullmatch(r"decision-(\d+)", name)
        if m and value in ("approve", "reject"):
            pid = int(m.group(1))
            decisions.append({"proposal": pid, "approve": value == "approve",
                              "digest": str(form.get(f"digest-{pid}", ""))})
    if not decisions:
        raise HTTPException(422, "no decision was chosen")
    from starlette.concurrency import run_in_threadpool
    await run_in_threadpool(decide_on_run, request.app.state.runtime, who, run_id, decisions)
    return RedirectResponse(f"/runs/{run_id}", status_code=303)


@router.get("/documents/{document_id}/pages/{page}", response_class=HTMLResponse)
def document_page(request: Request, document_id: int, page: int, q: str = "",
                  who: Principal = Depends(session_reviewer)):
    view = page_view(request.app.state.runtime, who.tenant_id, document_id, page)
    needle = _norm(q)
    marked = [(i, line, bool(needle) and (_norm(line) in needle or needle in _norm(line)) and len(_norm(line)) > 3)
              for i, line in enumerate(view["lines"], 1)]
    return _page(request, "document.html", d=view, lines=marked, q=q)


@router.get("/documents/{document_id}/file")
def document_file(request: Request, document_id: int, who: Principal = Depends(session_reviewer)):
    from .api import file_response
    return file_response(request.app.state.runtime, who.tenant_id, document_id)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()

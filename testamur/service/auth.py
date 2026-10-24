"""Who is calling, checked on every request.

- **API clients** send a key as a bearer token. The key is looked up by its hash, live, on every request: a revoked key
  fails on its next call, whatever process served the one before.
- **The review page** signs a session cookie (HttpOnly, SameSite=Strict, Secure outside a local demo) holding the
  hash of the key a reviewer signed in with, never the key, and looks that hash up live on every request the same way.
  Every form carries the session's CSRF token, compared in constant time.
- **Roles.** A key's role is fixed when it is made: an agent's key opens only the agent surface (MCP), never this
  service; an uploader's can upload and follow its uploads; only a reviewer's can read evidence, start investigations
  and approve. The person recorded on an approval is the key's principal, whatever a request body says.
"""
from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .. import agents
from ..agents import Principal

bearer = HTTPBearer(auto_error=False, description="A key made with `testamur keys create --role ...`")
SERVICE_ROLES = ("uploader", "reviewer")


def principal(request: Request, creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> Principal:
    if creds is None:
        raise HTTPException(401, "a bearer key is needed", headers={"WWW-Authenticate": "Bearer"})
    try:
        who = agents.principal_of(request.app.state.runtime.store, key=creds.credentials)
    except PermissionError:
        raise HTTPException(401, "unknown or revoked key", headers={"WWW-Authenticate": "Bearer"}) from None
    if who.role not in SERVICE_ROLES:
        raise HTTPException(403, f"a {who.role}'s key does not open this service")
    request.state.principal = who
    return who


def requires(*roles: str):
    def check(who: Principal = Depends(principal)) -> Principal:
        if who.role not in roles:
            raise HTTPException(403, f"this needs the {' or '.join(roles)} role")
        return who
    return check


class SignedOut(Exception):
    """No live reviewer session: the page sends the browser to sign in."""


def session_reviewer(request: Request) -> Principal:
    digest = request.session.get("key")
    if not digest:
        raise SignedOut()
    try:
        who = agents.principal_of(request.app.state.runtime.store, key_hash=digest)
    except PermissionError:
        request.session.clear()
        raise SignedOut() from None
    if who.role != "reviewer":
        request.session.clear()
        raise SignedOut()
    request.state.principal = who
    return who


def sign_in(request: Request, key: str) -> Principal:
    who = agents.principal_of(request.app.state.runtime.store, key=key)     # PermissionError if unknown or revoked
    if who.role != "reviewer":
        raise PermissionError("only a reviewer's key opens the review page")
    request.session.clear()
    request.session.update({"key": who.key_hash, "csrf": secrets.token_urlsafe(24)})
    return who


def csrf_token(request: Request) -> str:
    return request.session.get("csrf", "")


def check_csrf(request: Request, token: str) -> None:
    expected = request.session.get("csrf")
    if not expected or not token or not secrets.compare_digest(expected, token):
        raise HTTPException(403, "the form is stale or not from this site: reload the page")

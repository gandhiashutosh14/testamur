"""The service: FastAPI over the engine and the investigation workflow.

    uvicorn --factory testamur.service:create_app      (the Compose stack runs this; see docker-compose.yml)

The process holds no state of its own: the database (the owner's connection for keys and the queue, the application
role's under row-level security for everything a tenant reads or writes), the object store and the checkpoints are
shared, so any number of API and worker processes can run side by side.
"""
from __future__ import annotations

import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .. import __version__, migrations
from ..telemetry import configure, event, span
from . import api, web
from .auth import SignedOut
from .runtime import Runtime
from .settings import Settings

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
                               "frame-ancestors 'none'; form-action 'self'; base-uri 'none'",
    "Cache-Control": "no-store",
}


def create_app(settings: Settings | None = None, runtime: Runtime | None = None) -> FastAPI:
    settings = settings or (runtime.settings if runtime else Settings.from_env())
    configure("testamur-api")
    owned = runtime is None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        if owned:                                   # a runtime handed in (a test's) is its owner's to close
            app.state.runtime.close()

    app = FastAPI(title="TESTAMUR", version=__version__, lifespan=lifespan,
                  description="Claim verification for India's corporate insolvency process: uploads, evidence, an "
                              "investigator and a verifier agent, and a person's evidence-bound approval.",
                  docs_url="/docs", redoc_url=None)
    app.state.runtime = runtime or Runtime(settings)
    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, session_cookie="testamur_session",
                       max_age=8 * 3600, same_site="strict", https_only=settings.secure_cookies)

    @app.middleware("http")
    async def trace_and_harden(request: Request, call_next):
        t0 = time.perf_counter()
        with span("http.request", stage=request.method) as note:
            response = await call_next(request)
            route = request.scope.get("route")
            note(**{"http.route": getattr(route, "path", "unmatched"), "status": response.status_code})
            who = getattr(request.state, "principal", None)
            event("request", stage=request.method, status=response.status_code,
                  tenant_id=getattr(who, "tenant_id", None), **{"http.route": getattr(route, "path", "unmatched")},
                  ms=round(1000 * (time.perf_counter() - t0), 1))
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        return response

    @app.exception_handler(SignedOut)
    async def signed_out(request: Request, exc: SignedOut):
        return RedirectResponse("/login", status_code=303)

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"ok": True, "version": __version__}

    @app.get("/readyz", include_in_schema=False)
    def readyz():
        rt = app.state.runtime
        at, head = migrations.current(rt.settings.database_url), migrations.head()
        ok = at == head
        return JSONResponse({"ok": ok, "schema": at, "expected": head}, status_code=200 if ok else 503)

    app.include_router(api.router)
    app.include_router(web.router)
    app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
    return app

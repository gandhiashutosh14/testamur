"""The service end to end, in process: keys and roles checked on every request, each firm's data its own, an upload
stored, queued and read by a worker, an investigation queued from the API and run by a worker, and a reviewer's
decision applied once, against the evidence it was shown, under the reviewer's name. The review page signs a session
and refuses a form without its CSRF token."""
import random

import pytest
from fastapi.testclient import TestClient
from scripted import Agents, _conditional_authority, _pair
from sqlalchemy import select

from testamur import agents, jobs
from testamur.investigate import Investigations, sqlite_checkpointer
from testamur.service import Runtime, Settings, create_app
from testamur.store import AuditRow, Claim, Proposal
from testamur.synth.render import DocContent, render_pdf
from testamur.worker import work

SECRET = "s" * 40


@pytest.fixture
def svc(env, tmp_path):
    """The app over a processed tiny world, with keys for each role, and a worker to drain the queue."""
    world, store, engine = env
    made = []

    def build(model=None, secure=False):
        saver, conn = sqlite_checkpointer(tmp_path / "checkpoints.sqlite")
        made.append(conn)
        inv = Investigations(engine, model, saver)
        settings = Settings(database_url=str(store.engine.url), session_secret=SECRET, as_of=world.as_of,
                            secure_cookies=secure, max_upload_bytes=2 << 20)
        app = create_app(runtime=Runtime(settings, engine=engine, investigations=inv))
        keys = {role: agents.create_key(store, 1, f"{role}-1", role) for role in ("reviewer", "uploader", "agent")}
        keys["other-firm"] = agents.create_key(store, 2, "reviewer-2", "reviewer")
        client = TestClient(app, base_url="https://testserver" if secure else "http://testserver")
        client.__enter__()
        made.append(client)
        return client, keys, lambda: work(engine, "test-worker", investigations=inv)
    yield build
    for m in reversed(made):
        if isinstance(m, TestClient):
            m.__exit__(None, None, None)
        else:
            m.close()


def _as(key):
    return {"Authorization": f"Bearer {key}"}


def _sign_in(client, key):
    """Sign in as the page does: the form from GET /login, with the token it carries."""
    token = client.get("/login").text.split('name="csrf" value="')[1].split('"')[0]
    return client.post("/login", data={"key": key, "csrf": token}, follow_redirects=False)


def test_every_request_needs_a_live_key_of_a_role_this_service_serves(svc, env):
    client, keys, _ = svc()
    assert client.get("/api/v1/claims").status_code == 401
    assert client.get("/api/v1/claims", headers=_as("tsk_not-a-key")).status_code == 401
    refused = client.get("/api/v1/claims", headers=_as(keys["agent"]))                    # agents use MCP
    assert refused.status_code == 403 and "does not open this service" in refused.json()["detail"]
    assert client.get("/api/v1/claims", headers=_as(keys["reviewer"])).status_code == 200
    agents.revoke_key(env[1], 1, "reviewer-1", by="admin")
    assert client.get("/api/v1/claims", headers=_as(keys["reviewer"])).status_code == 401  # on the next call


def test_an_uploader_uploads_but_reads_no_evidence_and_approves_nothing(svc):
    client, keys, _ = svc()
    up = _as(keys["uploader"])
    assert client.get("/api/v1/me", headers=up).json() == {"tenant": 1, "name": "uploader-1", "role": "uploader"}
    for path in ("/api/v1/claims", "/api/v1/claims/1", "/api/v1/documents/1/pages/1", "/api/v1/proposals"):
        assert client.get(path, headers=up).status_code == 403, path
    assert client.post("/api/v1/claims/1/investigations", headers=up, json={}).status_code == 403
    assert client.post("/api/v1/investigations/1/decisions", headers=up,
                       json={"decisions": [{"proposal": 1, "approve": True, "digest": "x" * 64}]}).status_code == 403


def test_a_firm_sees_only_its_own_claims_documents_and_runs(svc, env):
    _, store, _ = env
    client, keys, _ = svc()
    with store.system() as s:
        theirs = s.scalar(select(Claim.id).where(Claim.tenant_id == 1).limit(1))
    other = _as(keys["other-firm"])
    assert client.get(f"/api/v1/claims/{theirs}", headers=other).status_code == 404
    assert client.get(f"/api/v1/claims/{theirs}", headers=_as(keys["reviewer"])).status_code == 200
    queued = client.post(f"/api/v1/claims/{theirs}/investigations", headers=_as(keys["reviewer"]), json={}).json()
    assert client.get(f"/api/v1/investigations/{queued['run']}", headers=other).status_code == 404
    assert client.post(f"/api/v1/claims/{theirs}/investigations", headers=other, json={}).status_code == 404
    ids = [c["claim"] for c in client.get("/api/v1/claims?limit=100", headers=other).json()["items"]]
    with store.system() as s:
        assert ids and set(ids) <= set(s.scalars(select(Claim.id).where(Claim.tenant_id == 2)))


def test_an_upload_is_stored_queued_and_read_by_a_worker(svc, env):
    world, store, _ = env
    client, keys, drain = svc()
    with store.tenant(1) as s:
        c = s.scalars(select(Claim)).first()
        matter, creditor = c.matter_id, c.creditor_id
    page = [("title", "LETTER OF AUTHORITY"), ("kv", "Authorised representative", "Kavya Rao"),
            ("kv", "Date of issue", world.as_of.isoformat())]
    pdf = render_pdf(DocContent([page]), random.Random(4))
    r = client.post(f"/api/v1/matters/{matter}/uploads", headers=_as(keys["uploader"]),
                    files={"file": ("authority.pdf", pdf, "application/pdf")},
                    data={"declared_creditor_id": str(creditor)})
    assert r.status_code == 202, r.text
    upload = r.json()["upload"]
    assert client.get(f"/api/v1/uploads/{upload}", headers=_as(keys["uploader"])).json()["job"]["status"] == "queued"
    assert drain()["done"] == 1
    done = client.get(f"/api/v1/uploads/{upload}", headers=_as(keys["uploader"])).json()
    assert done["job"]["status"] == "done" and len(done["documents"]) == 1
    assert client.get(f"/api/v1/uploads/{upload}", headers=_as(keys["other-firm"])).status_code == 404  # not theirs
    assert client.post(f"/api/v1/matters/{matter}/uploads", headers=_as(keys["uploader"]),
                       files={"file": ("big.pdf", b"%PDF" + b"0" * (2 << 20), "application/pdf")}).status_code == 413
    with store.tenant(1) as s:
        assert s.scalar(select(AuditRow).where(AuditRow.action == "uploaded",
                                               AuditRow.target == f"upload:{upload}")).actor == "uploader-1"


def test_an_investigation_runs_on_a_worker_and_a_reviewer_approves_what_they_were_shown(svc, env):
    _, store, _ = env
    claim, doc = _conditional_authority(env)
    investigator, verifier = _pair(doc)
    client, keys, drain = svc(Agents(investigator, verifier))
    rv = _as(keys["reviewer"])
    queued = client.post(f"/api/v1/claims/{claim}/investigations", headers=rv, json={"mode": "pair"})
    assert queued.status_code == 202
    run_id = queued.json()["run"]
    assert client.get(f"/api/v1/investigations/{run_id}", headers=rv).json()["status"] == "started"
    assert drain()["done"] == 1
    run = client.get(f"/api/v1/investigations/{run_id}", headers=rv).json()
    assert run["status"] == "awaiting_approval" and "thread_id" not in run
    rec = next(r for r in run["recommendations"] if r.get("proposal_id"))
    # a stale page: refused before the run resumes, so the run still waits for the right decision
    wrong = client.post(f"/api/v1/investigations/{run_id}/decisions", headers=rv, json={"decisions": [
        {"proposal": rec["proposal_id"], "approve": True, "digest": "0" * 64}]})
    assert wrong.status_code == 409
    # a run's proposal is decided on its run, not around it
    assert client.post(f"/api/v1/proposals/{rec['proposal_id']}/decision", headers=rv,
                       json={"approve": True, "digest": rec["digest"]}).status_code == 409
    ok = client.post(f"/api/v1/investigations/{run_id}/decisions", headers=rv, json={"decisions": [
        {"proposal": rec["proposal_id"], "approve": True, "digest": rec["digest"]}]})
    assert ok.status_code == 200 and ok.json()["status"] == "applied"
    assert ok.json()["outcomes"][0]["by"] == "reviewer-1"
    again = client.post(f"/api/v1/investigations/{run_id}/decisions", headers=rv, json={"decisions": [
        {"proposal": rec["proposal_id"], "approve": True, "digest": rec["digest"]}]})
    assert again.status_code == 409                                     # applied once
    with store.tenant(1) as s:
        p = s.get(Proposal, rec["proposal_id"])
        assert p.status == "applied" and p.decided_by == "reviewer-1"


def test_the_review_page_signs_a_session_and_refuses_forms_without_its_token(svc, env):
    claim, doc = _conditional_authority(env)
    client, keys, drain = svc(Agents(*_pair(doc)))
    assert client.get("/", follow_redirects=False).headers["location"] == "/login"
    refused = _sign_in(client, keys["uploader"])
    assert refused.status_code == 200 and "only a reviewer&#39;s key opens the review page" in refused.text
    assert "testamur_session" not in refused.cookies
    signed = _sign_in(client, keys["reviewer"])
    assert signed.status_code == 303
    cookie = signed.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and keys["reviewer"].lower() not in cookie
    page = client.get(f"/claims/{claim}")
    assert page.status_code == 200 and "Investigate" in page.text
    assert client.post(f"/claims/{claim}/investigate", data={"mode": "pair"}).status_code == 403   # no token
    token = page.text.split('name="csrf" value="')[1].split('"')[0]
    started = client.post(f"/claims/{claim}/investigate", data={"mode": "pair", "csrf": token},
                          follow_redirects=False)
    assert started.status_code == 303
    drain()
    run_page = client.get(started.headers["location"])
    assert "Your decision" in run_page.text and "document" in run_page.text
    assert run_page.headers["content-security-policy"].startswith("default-src 'self'")
    assert run_page.headers["x-frame-options"] == "DENY"
    agents.revoke_key(env[1], 1, "reviewer-1", by="admin")
    assert client.get(f"/claims/{claim}", follow_redirects=False).headers["location"] == "/login"


def test_secure_cookies_outside_a_local_demo(svc):
    client, keys, _ = svc(secure=True)
    signed = _sign_in(client, keys["reviewer"])
    assert signed.status_code == 303 and "secure" in signed.headers["set-cookie"].lower()


def test_signing_in_needs_the_sign_in_forms_own_token(svc):
    """Another site cannot sign a reviewer's browser in as someone else: the form carries a token of the session."""
    client, keys, _ = svc()
    assert client.post("/login", data={"key": keys["reviewer"]}, follow_redirects=False).status_code == 403
    client.get("/login")
    assert client.post("/login", data={"key": keys["reviewer"], "csrf": "forged"},
                       follow_redirects=False).status_code == 403
    assert _sign_in(client, keys["reviewer"]).status_code == 303


def test_only_the_api_documentation_page_may_load_from_a_cdn(svc):
    client, _, _ = svc()
    docs, login = client.get("/docs"), client.get("/login")
    assert docs.status_code == 200 and "cdn.jsdelivr.net" in docs.headers["content-security-policy"]
    assert "cdn.jsdelivr.net" not in login.headers["content-security-policy"]


def test_the_api_is_described_by_its_openapi_schema(svc):
    client, _, _ = svc()
    paths = client.get("/openapi.json").json()["paths"]
    for p in ("/api/v1/matters/{matter_id}/uploads", "/api/v1/claims/{claim_id}/investigations",
              "/api/v1/investigations/{run_id}/decisions", "/api/v1/documents/{document_id}/pages/{page}"):
        assert p in paths
    assert "/login" not in paths and "/healthz" not in paths


def test_jobs_name_their_firm_and_kind(svc, env):
    _, store, _ = env
    claim, _ = _conditional_authority(env)
    client, keys, _ = svc()
    client.post(f"/api/v1/claims/{claim}/investigations", headers=_as(keys["reviewer"]), json={})
    with store.system() as s:
        job = s.scalar(select(jobs.Job).where(jobs.Job.kind == "investigate"))
        assert job.tenant_id == 1 and job.key.startswith("run:") and job.max_attempts == 3

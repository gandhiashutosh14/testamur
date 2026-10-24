"""Drive the running Compose stack end to end, as a client would: the stack's CI job runs this after `docker compose
up`. Standard library only, so it runs on the host without the package.

    python scripts/smoke_stack.py --reviewer KEY --uploader KEY --other-firm KEY

Checks, in order: the API is ready at the schema head; the seed's uploads were read by the worker (claims have
statuses); an upload is stored in the object store, queued and read; an investigation is queued, executed by the
worker through its Postgres checkpoints and ends with a status and a trace; another firm's key sees none of it; and
the API's and the worker's spans reached Jaeger."""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid

API = "http://127.0.0.1:8000"
JAEGER = "http://127.0.0.1:16686"
FINAL = {"applied", "rejected", "no_change", "needs_person", "stale", "cancelled", "failed", "awaiting_approval"}


def call(method: str, path: str, key: str | None = None, body: dict | None = None, raw: bytes | None = None,
         content_type: str | None = None, base: str | None = None) -> tuple[int, dict]:
    base = base or API
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    data = None
    if body is not None:
        data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
    elif raw is not None:
        data, headers["Content-Type"] = raw, content_type
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except json.JSONDecodeError:                  # an error page, not JSON
            return e.code, {}


def wait(what: str, check, seconds: float, every: float = 2.0):
    deadline = time.monotonic() + seconds
    while True:
        try:
            out = check()
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            out = None
        if out:
            print(f"ok: {what}")
            return out
        if time.monotonic() > deadline:
            sys.exit(f"FAILED: {what} (gave up after {seconds:.0f}s)")
        time.sleep(every)


def traced_services() -> set[str]:
    """The services Jaeger holds traces of: its query API v3 (Jaeger 2), or the older one."""
    code, body = call("GET", "/api/v3/services", base=JAEGER)
    if code == 200:
        return set(body.get("services") or [])
    return set(call("GET", "/api/services", base=JAEGER)[1].get("data") or [])


def minimal_pdf(text: str) -> bytes:
    """A one-page PDF with a text layer, written by hand (no PDF library on the host)."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
               b"/Resources << /Font << /F1 5 0 R >> >> >>",
               b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def multipart(field: str, filename: str, data: bytes) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{filename}\"\r\n"
            f"Content-Type: application/pdf\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


def main() -> None:
    global API
    ap = argparse.ArgumentParser()
    ap.add_argument("--reviewer", required=True)
    ap.add_argument("--uploader", required=True)
    ap.add_argument("--other-firm", required=True)
    ap.add_argument("--api", default=API, help="the service's address")
    ap.add_argument("--skip-traces", action="store_true", help="no Jaeger to check (scripts/dev_service.py)")
    a = ap.parse_args()
    API = a.api.rstrip("/")

    wait("the API is ready at the schema head", lambda: call("GET", "/readyz")[1].get("ok"), 180)
    status, me = call("GET", "/api/v1/me", a.reviewer)
    assert status == 200 and me["role"] == "reviewer" and me["tenant"] == 1, me

    def statuses():
        code, page = call("GET", "/api/v1/claims?limit=100", a.reviewer)
        items = page.get("items", []) if code == 200 else []
        return items if items and all(c["statuses"] for c in items) else None
    claims = wait("the worker read the seed's uploads: every claim has statuses", statuses, 600, 5)

    claim = claims[0]["claim"]
    detail = call("GET", f"/api/v1/claims/{claim}", a.reviewer)[1]
    body, ctype = multipart("file", "authority.pdf", minimal_pdf("LETTER OF AUTHORITY"))
    status, up = call("POST", f"/api/v1/matters/{detail['matter']}/uploads", a.uploader, raw=body, content_type=ctype)
    assert status == 202, (status, up)
    done = wait("an upload went to the object store, the queue and the worker",
                lambda: (lambda u: u if u.get("job", {}).get("status") == "done" else None)(
                    call("GET", f"/api/v1/uploads/{up['upload']}", a.uploader)[1]), 180)
    assert done["documents"], done

    status, queued = call("POST", f"/api/v1/claims/{claim}/investigations", a.reviewer, body={"mode": "pair"})
    assert status == 202, (status, queued)
    run = wait("the worker executed the investigation to a status",
               lambda: (lambda r: r if r.get("status") in FINAL else None)(
                   call("GET", f"/api/v1/investigations/{queued['run']}", a.reviewer)[1]), 300)
    assert run["trace"], run
    print(f"    investigation {run['run']}: {run['status']}; {len(run['trace'])} trace event(s)")

    assert call("GET", f"/api/v1/claims/{claim}", a.other_firm)[0] == 404
    assert call("GET", f"/api/v1/investigations/{queued['run']}", a.other_firm)[0] == 404
    assert call("GET", "/api/v1/claims", a.uploader)[0] == 403
    print("ok: another firm sees none of it; an uploader reads no evidence")

    if a.skip_traces:
        print("the service works end to end (traces not checked)")
        return
    wait("the API's and the worker's spans reached Jaeger",
         lambda: {"testamur-api", "testamur-worker"} <= traced_services(), 120, 5)
    print("the stack works end to end")


if __name__ == "__main__":
    main()

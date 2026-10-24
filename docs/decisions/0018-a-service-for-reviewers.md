# 0018: A service for reviewers: stateless processes over a shared database, queue, object store and checkpoints

## Context
Until v0.5 people worked through the command line and agents through the MCP server. A review team needs a place to
read evidence, ask the agents for an investigation and approve what they propose, from a browser, with sign-in; other
systems need an API. The engine already had the hard parts (tenant sessions, a fenced job queue, atomic approvals
bound to evidence, durable investigations). The question was how to put a service around it without weakening any of
them.

## Decision
- **FastAPI, two surfaces** (`testamur.service`). A JSON API under `/api/v1` (OpenAPI at `/docs`) and a server-rendered
  review page. Both read through the same functions and decide through the same calls: the page adds nothing the API
  cannot do and bypasses nothing the API enforces.
- **Keys with roles, checked on every request.** A key is made for one principal of one firm with a role: `agent` (the
  MCP surface only), `uploader` (upload and follow uploads) or `reviewer` (read evidence, investigate, approve). It is
  looked up by hash on every request, so a revocation takes effect on the next one. An agent's key opens no part of the
  service and a person's key does not open the agent surface, so nothing an agent did is recorded under a person's
  name. The person recorded on an approval is the key's principal, whatever a request body says.
- **The review page signs a session**, HttpOnly, SameSite=Strict, Secure outside a local demo, holding the hash of the
  reviewer's key (never the key), looked up live on every request like a bearer key. Every form carries the session's
  CSRF token, compared in constant time. Pages send a strict Content-Security-Policy (no inline script or style), deny
  framing, and documents are only ever downloaded, never rendered in the service's origin.
- **Every route acts for the key's firm.** No route takes a firm from its path or body; another firm's claim, document,
  upload or run is a 404, not a 403, so a firm learns nothing about another's identifiers.
- **Slow work is queued; decisions are not.** An upload is stored (local files or any S3-compatible store behind one
  interface) and queued; reading it is a worker's job. An investigation is created in the request and executed by a
  worker, which keeps its lease renewed from a thread and stops between agent steps if the lease is lost. A reviewer's
  decision is applied in the request, atomically (ADR 0014), after its digest is checked against the proposal, before
  the paused run resumes: a stale page gets a 409 and the run still waits for the right decision.
- **Processes hold no state.** The database (the owner's connection for keys and the queue, the application role's
  under row-level security for every tenant session), the object store and the workflow's checkpoints (Postgres, via
  LangGraph's Postgres saver) are shared, so any number of API and worker processes can run, and any of them can
  continue a run another started.
- **The schema is migrated, not created.** Alembic migrations bring a database to the models exactly (a test compares
  them); a later migration that adds a tenant table must add its row-level security, which a test checks for every
  tenant table. The service refuses to start on a database behind the code.
- **Traced end to end.** One OpenTelemetry trace per request and per job, with a span per pipeline stage, agent step,
  tool call, proposal and approval. Spans carry identifiers and measurements only (an allow-list; errors by type, never
  their message), JSON logs carry the trace id, and the Compose stack exports to Jaeger.
- **One command.** `docker compose up --build` starts Postgres, the object store (RustFS), Jaeger, the migrations, the
  API and a worker; `seed` loads a synthetic world through the intake and prints keys. CI starts the same stack and
  drives it as a client: upload, read, investigate, isolation and traces.

## Consequences
- The stack demonstrates the deployment shape (stateless processes, shared state, a restricted database role), not a
  hardened deployment: there is no identity provider, rate limiting, TLS termination, backup or autoscaling here.
- Keys for people are a demo's sign-in. A deployment would put single sign-on in front and map its identities to
  principals; the per-request lookup and the roles stay as they are.
- The investigation needs a model. Without one configured, a run ends by routing its claim to a person, which is what
  the stack's CI job exercises.

## Alternatives considered
- *Run investigations inside the request.* A model step takes seconds to minutes; holding a request open for that is
  fragile, and a crashed API process would lose the run. Queued, any worker continues it from its checkpoint.
- *A single-page app.* More to build and to secure for the same reading and approving; the server-rendered page needs
  no script at all and works under the strictest policy.
- *Sessions stored server-side.* Equivalent here because the signed cookie holds only a hash that is looked up on every
  request; a revoked key ends its sessions without a session table.

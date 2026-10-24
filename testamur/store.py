"""Storage, with tenant isolation enforced by the session rather than by remembering to filter, and by the database
underneath it.

Every tenant-owned table carries `tenant_id`. A session opened for a tenant gets that tenant's filter added to every
ORM statement it executes (reads, and bulk UPDATE and DELETE too), refuses statements the filter cannot reach (Core
and textual SQL, bulk INSERT and upserts, bulk updates by primary key, an UPDATE that moves rows to another tenant),
and refuses to flush, or delete, a row that belongs to another tenant. A session opened without a tenant cannot touch
tenant data at all: it fails closed. Only the explicit system session (loading the registry, the job queue, the
harness) works across tenants, and it says so at the call site.

Underneath the session, the database holds the same line: a reference from one tenant's row to another row (a claim's
matter, a verdict's document) is a foreign key on (tenant_id, id), so a row cannot point into another tenant even by a
bug that bypasses the session; and on Postgres, row-level security under a restricted application role refuses other
tenants' rows to any statement at all (`enable_row_level_security`).

SQLite locally (WAL mode); the same models run on Postgres."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker, with_loader_criteria

TENANT_SAFE = "tenant_safe"     # execution option: a statement a tenant session issues on purpose (an advisory lock)


class TenantScopeError(RuntimeError):
    """Raised when tenant data is read or written without the right tenant in scope."""


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


def _tenant_fk() -> ForeignKey:
    # deferred: checked at commit, so a transaction may add a parent and its children in any order
    return ForeignKey("tenants.id", deferrable=True, initially="DEFERRED")


def _same_tenant(column: str, target: str) -> ForeignKeyConstraint:
    """A reference to another tenant-owned row, which must be the same tenant's: (tenant_id, column) refers to
    (tenant_id, id) of the target. A null reference is not checked (nothing is referred to)."""
    return ForeignKeyConstraint(["tenant_id", column], [f"{target}.tenant_id", f"{target}.id"],
                                deferrable=True, initially="DEFERRED")


def _referable(table: str) -> UniqueConstraint:
    """(tenant_id, id) as a key other tables refer to."""
    return UniqueConstraint("tenant_id", "id", name=f"uq_{table}_tenant_id")


class TenantScoped:
    """Mixin: rows that belong to exactly one tenant."""
    tenant_id: Mapped[int] = mapped_column(_tenant_fk(), index=True)


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))


class Matter(TenantScoped, Base):
    """One corporate insolvency resolution process: the corporate debtor and the dates its clock runs from."""
    __tablename__ = "matters"
    __table_args__ = (_referable("matters"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))                     # the corporate debtor
    debtor_cin: Mapped[str] = mapped_column(String(40), default="")
    commencement: Mapped[date] = mapped_column(Date)                   # insolvency commencement date
    announced: Mapped[date] = mapped_column(Date)                      # public announcement
    claims_due: Mapped[date] = mapped_column(Date)                     # last date for claims in the announcement


class Creditor(TenantScoped, Base):
    __tablename__ = "creditors"
    __table_args__ = (_referable("creditors"), _same_tenant("trustee_id", "creditors"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    legal_name: Mapped[str] = mapped_column(String(300))
    address: Mapped[str] = mapped_column(String(300), default="")
    cin: Mapped[str | None] = mapped_column(String(40), nullable=True)
    gstin: Mapped[str | None] = mapped_column(String(40), nullable=True)
    lei: Mapped[str | None] = mapped_column(String(40), nullable=True)
    account_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    ifsc: Mapped[str | None] = mapped_column(String(20), nullable=True)
    trustee_id: Mapped[int | None] = mapped_column(Integer, nullable=True)   # the debenture trustee acting for it


class Claim(TenantScoped, Base):
    """A financial creditor's claim in one process. The amounts come from the corporate debtor's records: what its
    books show as owed, and the facility the creditor sanctioned."""
    __tablename__ = "claims"
    __table_args__ = (_referable("claims"), _same_tenant("matter_id", "matters"),
                      _same_tenant("creditor_id", "creditors"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    matter_id: Mapped[int] = mapped_column(Integer)
    creditor_id: Mapped[int] = mapped_column(Integer)
    tier: Mapped[str] = mapped_column(String(20))
    claim_type: Mapped[str] = mapped_column(String(40))                # secured | unsecured
    facility_type: Mapped[str] = mapped_column(String(60))
    book_balance: Mapped[int] = mapped_column(BigInteger)              # rupees
    sanctioned: Mapped[int] = mapped_column(BigInteger)                # rupees


class Attestation(TenantScoped, Base):
    """A requirement verified outside the platform (a call-back, a register search). For evidence that counts in one
    process only, it must name the claim; one naming only the creditor vouches for creditor-wide evidence only."""
    __tablename__ = "attestations"
    __table_args__ = (_same_tenant("creditor_id", "creditors"), _same_tenant("claim_id", "claims"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    creditor_id: Mapped[int] = mapped_column(Integer)
    claim_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    requirement_id: Mapped[str] = mapped_column(String(60))
    verified_by: Mapped[str] = mapped_column(String(120))
    verified_on: Mapped[date] = mapped_column(Date)
    note: Mapped[str] = mapped_column(Text, default="")


class Blob(TenantScoped, Base):
    """Content-addressed file store. Deduplication is per tenant: sharing across tenants would save a little and
    reveal to one tenant that another holds the same file."""
    __tablename__ = "blobs"
    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[int] = mapped_column(_tenant_fk(), primary_key=True)
    size: Mapped[int] = mapped_column(Integer)
    mime: Mapped[str] = mapped_column(String(60))
    path: Mapped[str] = mapped_column(String(400))
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class UploadRow(TenantScoped, Base):
    __tablename__ = "uploads"
    __table_args__ = (_same_tenant("matter_id", "matters"), _same_tenant("declared_creditor_id", "creditors"),
                      ForeignKeyConstraint(["sha256", "tenant_id"], ["blobs.sha256", "blobs.tenant_id"],
                                           deferrable=True, initially="DEFERRED"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    matter_id: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    filename: Mapped[str] = mapped_column(String(300))
    channel: Mapped[str] = mapped_column(String(40))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime)
    declared_creditor_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    external_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)


class ReadingLock(TenantScoped, Base):
    """A worker reading a file right now. Another worker that needs the same file waits instead of paying for the
    same OCR twice; a lock not renewed for LOCK_SECONDS (engine.py) is a dead worker's and may be taken over. `owner`
    fences it: only the worker holding the current token may renew or release it, so a worker that comes back after
    its lock was taken over cannot release the new owner's lock."""
    __tablename__ = "reading_locks"
    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[int] = mapped_column(_tenant_fk(), primary_key=True)
    owner: Mapped[str] = mapped_column(String(40), default="")
    locked_at: Mapped[datetime] = mapped_column(DateTime)


class Document(TenantScoped, Base):
    """A file as evidence in one process: one per distinct file per matter. The bytes (Blob) and their reading
    (ExtractionRow) are stored once per tenant whatever the context; whose evidence the file is, and for which claims
    it counts, is decided in the context it was filed in. The same bytes filed in two processes are two documents (each
    judged against its own process's dates and debtor); re-uploading them in the same process adds an upload event."""
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("tenant_id", "matter_id", "sha256"), _referable("documents"),
                      _same_tenant("creditor_id", "creditors"), _same_tenant("matter_id", "matters"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    matter_id: Mapped[int] = mapped_column(Integer, index=True)
    sha256: Mapped[str] = mapped_column(String(64))
    doc_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    class_method: Mapped[str] = mapped_column(String(40), default="")
    class_score: Mapped[float] = mapped_column(Float, default=0.0)
    creditor_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    covers: Mapped[list[Any]] = mapped_column(JSON, default=list)
    match_method: Mapped[str] = mapped_column(String(40), default="")
    match_score: Mapped[float] = mapped_column(Float, default=0.0)
    match_detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    fields: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    conflicts: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    identifiers: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    hostile: Mapped[bool] = mapped_column(Boolean, default=False)
    cleared: Mapped[bool] = mapped_column(Boolean, default=False)       # a person read it despite the finding
    state: Mapped[str] = mapped_column(String(30), default="new")     # matched | needs_review | unknown | rejected
    engine_version: Mapped[str] = mapped_column(String(80), default="")
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ExtractionRow(TenantScoped, Base):
    """OCR and text-layer output, keyed by content and extractor version: the same pages are never paid for twice."""
    __tablename__ = "extractions"
    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[int] = mapped_column(_tenant_fk(), primary_key=True)
    extractor: Mapped[str] = mapped_column(String(60), primary_key=True)
    version: Mapped[str] = mapped_column(String(40), primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    ocr_pages: Mapped[int] = mapped_column(Integer, default=0)
    seconds: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class VerdictRow(TenantScoped, Base):
    __tablename__ = "verdicts"
    __table_args__ = (UniqueConstraint("tenant_id", "claim_id", "requirement_id", "check_id", "document_id",
                                       "engine_version"),
                      _same_tenant("claim_id", "claims"), _same_tenant("document_id", "documents"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    claim_id: Mapped[int] = mapped_column(Integer, index=True)
    requirement_id: Mapped[str] = mapped_column(String(60))
    check_id: Mapped[str] = mapped_column(String(60))
    document_id: Mapped[int] = mapped_column(Integer, index=True)
    verdict: Mapped[str] = mapped_column(String(10))
    severity: Mapped[str] = mapped_column(String(10))
    tier: Mapped[str] = mapped_column(String(10))
    reason: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[list[Any]] = mapped_column(JSON, default=list)
    fix_hint: Mapped[str | None] = mapped_column(Text, nullable=True)
    transient: Mapped[bool] = mapped_column(Boolean, default=False)
    lapsed: Mapped[bool] = mapped_column(Boolean, default=False)
    engine_version: Mapped[str] = mapped_column(String(80))
    as_of: Mapped[date | None] = mapped_column(Date, nullable=True)     # set when the verdict depends on the date
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class CheckReview(TenantScoped, Base):
    """A person's verdict on one check, for one claim and document. It replaces the engine's verdict for as
    long as the check still means what it meant when the person decided (its rule digest), across re-runs and engine
    versions. The engine's verdict at the time is kept: comparing the two is per-check accuracy. `kind` says how
    the verdict reached the person: from the review queue (the engine could not decide) or from an audit sample
    (a random draw of the engine's conclusive verdicts, the unbiased basis for accuracy estimates)."""
    __tablename__ = "check_reviews"
    __table_args__ = (_same_tenant("claim_id", "claims"), _same_tenant("document_id", "documents"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    claim_id: Mapped[int] = mapped_column(Integer, index=True)
    requirement_id: Mapped[str] = mapped_column(String(60))
    check_id: Mapped[str] = mapped_column(String(60), index=True)
    document_id: Mapped[int] = mapped_column(Integer, index=True)
    verdict: Mapped[str] = mapped_column(String(10))            # PASS | FAIL: a person decides, or leaves it queued
    machine_verdict: Mapped[str] = mapped_column(String(10))
    rule_digest: Mapped[str] = mapped_column(String(16))
    as_of: Mapped[date | None] = mapped_column(Date, nullable=True)     # set when the check depends on the date
    kind: Mapped[str] = mapped_column(String(10), default="queue")
    reviewer: Mapped[str] = mapped_column(String(300))
    note: Mapped[str] = mapped_column(Text, default="")
    # the agent proposal this decision applied, if any: unique, so a proposal's effect exists at most once
    proposal_id: Mapped[int | None] = mapped_column(Integer, nullable=True, unique=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class FieldCorrection(TenantScoped, Base):
    """A person's reading of one field, used instead of what extraction read. The rules then judge it like any
    value, so a correction made today is judged by tomorrow's rules too. The machine's reading is kept."""
    __tablename__ = "field_corrections"
    __table_args__ = (_same_tenant("document_id", "documents"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(Integer, index=True)
    field: Mapped[str] = mapped_column(String(60))
    raw: Mapped[str] = mapped_column(Text)
    machine_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewer: Mapped[str] = mapped_column(String(300))
    note: Mapped[str] = mapped_column(Text, default="")
    proposal_id: Mapped[int | None] = mapped_column(Integer, nullable=True, unique=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class CheckEvent(TenantScoped, Base):
    """A person re-certifying a check after a breach, having found the cause: its accuracy is measured afresh from
    the reviews after `after_review_id`."""
    __tablename__ = "check_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    check_id: Mapped[str] = mapped_column(String(60), index=True)
    action: Mapped[str] = mapped_column(String(20))
    after_review_id: Mapped[int] = mapped_column(Integer, default=0)
    reviewer: Mapped[str] = mapped_column(String(300))
    note: Mapped[str] = mapped_column(Text, default="")
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class DocumentReview(TenantScoped, Base):
    """A person's decision about a whole document: its type (classify), whose evidence it is (assign), that it is
    nobody's evidence (reject), or that it carries text aimed at automated readers and a person read it anyway
    (clear). The latest decision of each kind stands; assign and reject replace each other."""
    __tablename__ = "document_reviews"
    __table_args__ = (_same_tenant("document_id", "documents"), _same_tenant("creditor_id", "creditors"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(Integer, index=True)
    action: Mapped[str] = mapped_column(String(10))
    doc_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    creditor_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    machine: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    reviewer: Mapped[str] = mapped_column(String(300))
    note: Mapped[str] = mapped_column(Text, default="")
    proposal_id: Mapped[int | None] = mapped_column(Integer, nullable=True, unique=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class RequirementState(TenantScoped, Base):
    __tablename__ = "requirement_states"
    __table_args__ = (_same_tenant("claim_id", "claims"), _same_tenant("document_id", "documents"))
    claim_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    requirement_id: Mapped[str] = mapped_column(String(60), primary_key=True)
    tenant_id: Mapped[int] = mapped_column(_tenant_fk(), primary_key=True)
    status: Mapped[str] = mapped_column(String(40))
    document_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    via_trustee: Mapped[bool] = mapped_column(Boolean, default=False)
    reasons: Mapped[list[Any]] = mapped_column(JSON, default=list)
    failing: Mapped[list[Any]] = mapped_column(JSON, default=list)      # checks that FAIL, for findings that act
    version: Mapped[int] = mapped_column(Integer, default=0)            # one more for every change reported
    # a fingerprint of everything the status rests on (the document and its version, every verdict with its reason
    # and evidence, people's decisions, the engine): it changes when the evidence changes, whatever the status does.
    # A proposal is bound to it; a person's confirmation of a proposal made on other evidence is refused
    evidence_digest: Mapped[str] = mapped_column(String(16), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class OutboxEvent(TenantScoped, Base):
    """Written in the same transaction as the status change it reports: no change without its event, and no event
    without its change. The dispatcher (actions.py) turns events into work; `key` makes each event unique."""
    __tablename__ = "outbox"
    __table_args__ = (UniqueConstraint("tenant_id", "key"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Task(TenantScoped, Base):
    """Remediation work for a person: chase a creditor for evidence, or follow a finding up. At most one open task
    per claim and requirement, enforced by the database, not by remembering to check."""
    __tablename__ = "tasks"
    __table_args__ = (Index("one_open_task", "tenant_id", "claim_id", "requirement_id", unique=True,
                            sqlite_where=text("status = 'open'"), postgresql_where=text("status = 'open'")),
                      _same_tenant("claim_id", "claims"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    claim_id: Mapped[int] = mapped_column(Integer, index=True)
    requirement_id: Mapped[str] = mapped_column(String(60))
    kind: Mapped[str] = mapped_column(String(20))                       # chase_creditor | follow_up
    title: Mapped[str] = mapped_column(Text)
    detail: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(10), default="open")    # open | done | superseded
    opened_at: Mapped[datetime] = mapped_column(DateTime)
    due_at: Mapped[datetime] = mapped_column(DateTime)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    closed_reason: Mapped[str] = mapped_column(Text, default="")
    event_key: Mapped[str] = mapped_column(String(120))


class Notification(TenantScoped, Base):
    """A message to a creditor, kept as written. `key` makes each one unique; a cooldown keeps reminders apart."""
    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("tenant_id", "key"), _same_tenant("claim_id", "claims"),
                      _same_tenant("creditor_id", "creditors"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(160))
    creditor_id: Mapped[int] = mapped_column(Integer)
    claim_id: Mapped[int] = mapped_column(Integer, index=True)
    requirement_id: Mapped[str] = mapped_column(String(60))
    subject: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ClaimPermission(TenantScoped, Base):
    """Whether a claim's evidence permits an action (to vote, to be paid), and which conditions are unmet. Computed by
    the engine whenever a status changes, and reported through the outbox like a status. A recommendation: the action
    itself is taken elsewhere, by a person."""
    __tablename__ = "claim_permissions"
    __table_args__ = (_same_tenant("claim_id", "claims"),)
    claim_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    action: Mapped[str] = mapped_column(String(20), primary_key=True)
    tenant_id: Mapped[int] = mapped_column(_tenant_fk(), primary_key=True)
    granted: Mapped[bool] = mapped_column(Boolean, default=False)
    unmet: Mapped[list[Any]] = mapped_column(JSON, default=list)
    version: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Hold(TenantScoped, Base):
    """A hold on one action for a claim while its evidence does not permit it: its votes are not counted, or nothing is
    paid to it. Placed and released by the dispatcher from the claim's permissions, announced by webhook; reversible by
    design. At most one open hold per claim and action."""
    __tablename__ = "holds"
    __table_args__ = (Index("one_open_hold", "tenant_id", "claim_id", "action", unique=True,
                            sqlite_where=text("released_at IS NULL"), postgresql_where=text("released_at IS NULL")),
                      _same_tenant("claim_id", "claims"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    claim_id: Mapped[int] = mapped_column(Integer, index=True)
    action: Mapped[str] = mapped_column(String(20), default="vote")
    reasons: Mapped[list[Any]] = mapped_column(JSON, default=list)        # the conditions unmet when it was placed
    placed_at: Mapped[datetime] = mapped_column(DateTime)
    released_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class WebhookEndpoint(TenantScoped, Base):
    """Where a tenant's own systems (case management, ticketing) want to hear about changes; messages are signed with
    the endpoint's secret (Standard Webhooks)."""
    __tablename__ = "webhook_endpoints"
    __table_args__ = (_referable("webhook_endpoints"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    url: Mapped[str] = mapped_column(String(400))
    secret: Mapped[str] = mapped_column(String(120))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Delivery(TenantScoped, Base):
    """One message to one endpoint, retried with backoff until delivered or dead. `webhook_id` is stable across
    retries, so the receiver can ignore a message it has already processed: delivery is at least once, the effect
    exactly once."""
    __tablename__ = "deliveries"
    __table_args__ = (UniqueConstraint("tenant_id", "endpoint_id", "webhook_id"),
                      _same_tenant("endpoint_id", "webhook_endpoints"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    endpoint_id: Mapped[int] = mapped_column(Integer)
    webhook_id: Mapped[str] = mapped_column(String(160))
    event_type: Mapped[str] = mapped_column(String(60))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(10), default="pending")      # pending | delivered | dead
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")


class ModelCall(TenantScoped, Base):
    """Every language-model call, keyed by the model and the exact prompt: the idempotency gate at the provider
    boundary. A repeated question is answered from here and not paid for again."""
    __tablename__ = "model_calls"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[int] = mapped_column(_tenant_fk(), primary_key=True)
    model: Mapped[str] = mapped_column(String(120))
    response: Mapped[str] = mapped_column(Text)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    seconds: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class UsageRow(TenantScoped, Base):
    """Spend, or spend reserved before a paid step runs (budget.reserve). A reservation is settled to what was spent,
    released if the step failed, or, if the worker died holding it, marked abandoned when another worker takes its
    lock over: still counted (the pages may have been paid for), but no longer pending."""
    __tablename__ = "usage"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    day: Mapped[date] = mapped_column(Date)
    provider: Mapped[str] = mapped_column(String(20))     # ocr | model
    units: Mapped[float] = mapped_column(Float)            # OCR pages, or model tokens
    cached: Mapped[bool] = mapped_column(Boolean, default=False)
    state: Mapped[str] = mapped_column(String(10), default="spent")      # spent | reserved | abandoned
    reserved_by: Mapped[str | None] = mapped_column(String(40), nullable=True)    # the reading lock's owner token


class TenantBudget(TenantScoped, Base):
    """Per-tenant daily spend caps for the paid steps. None means uncapped. A tenant over its cap waits for the
    next day; its work is deferred, never dropped."""
    __tablename__ = "budgets"
    tenant_id: Mapped[int] = mapped_column(_tenant_fk(), primary_key=True)
    ocr_pages_per_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_tokens_per_day: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ApiKey(TenantScoped, Base):
    """An agent's key: the tenant it acts for and the name it acts under. Only a hash of the key is stored."""
    __tablename__ = "api_keys"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ToolGrant(TenantScoped, Base):
    """The tenant's tool catalogue: an agent may call a tool only while the tenant has granted it, to every agent of
    the tenant (`principal` "*") or to one agent by name (an investigator and a verifier hold different tools)."""
    __tablename__ = "tool_grants"
    __table_args__ = (UniqueConstraint("tenant_id", "tool", "principal"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tool: Mapped[str] = mapped_column(String(60))
    principal: Mapped[str] = mapped_column(String(80), default="*")
    granted_by: Mapped[str] = mapped_column(String(300))
    granted_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Proposal(TenantScoped, Base):
    """A change an agent asked for. Nothing happens until a person confirms it; it is then applied through the same
    engine call a person would make, and audited with both names. Unconfirmed proposals expire.

    `binding` is the evidence the proposal was made on and the person is shown: the document and its bytes, the
    requirement's evidence fingerprint, the check's rule digest, the engine. Confirmation re-derives it in the same
    transaction that applies the change, and a proposal whose evidence moved on is refused as stale, never applied to
    whatever is current now. `binding_digest` is what the person confirms."""
    __tablename__ = "proposals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(30))
    args: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    binding: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    binding_digest: Mapped[str] = mapped_column(String(16), default="")
    summary: Mapped[str] = mapped_column(Text)
    proposed_by: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(10), default="proposed")   # proposed | applied | declined | expired | stale
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    decided_by: Mapped[str | None] = mapped_column(String(300), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    outcome: Mapped[str] = mapped_column(Text, default="")


class Setting(Base):
    """Facts about a database that later commands need: the as-of date it was evaluated at, where its files are."""
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class AuditRow(TenantScoped, Base):
    __tablename__ = "audit"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor: Mapped[str] = mapped_column(String(300))
    action: Mapped[str] = mapped_column(String(80))
    target: Mapped[str] = mapped_column(String(200))
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


def _tenant_target(state) -> bool:
    mapper = state.bind_mapper
    return mapper is not None and issubclass(mapper.class_, TenantScoped)


def _sets_tenant(statement) -> bool:
    return any(getattr(k, "key", k) == "tenant_id" for k in (getattr(statement, "_values", None) or {}))


@event.listens_for(Session, "do_orm_execute")
def _scope_statements(state):
    """Every statement a tenant session executes is either scoped to the tenant or refused. The filter is added to
    ORM reads and to ORM bulk UPDATE and DELETE alike (a bulk statement never passes through the flush checks below);
    what the filter cannot reach is refused rather than trusted."""
    session = state.session
    if session.info.get("system"):
        return
    tenant_id = session.info.get("tenant_id")
    if tenant_id is None:
        raise TenantScopeError("tenant data accessed in a session with no tenant")
    if state.execution_options.get(TENANT_SAFE):
        return
    if not state.is_orm_statement:
        raise TenantScopeError("a Core or textual statement in a tenant session would bypass the tenant filter; use "
                               "the ORM, or a system session")
    if state.is_insert:
        if _tenant_target(state):
            raise TenantScopeError("a bulk INSERT or upsert of tenant rows in a tenant session; add the objects to the "
                                   "session instead, where every row is checked")
        return
    if (state.is_update or state.is_delete) and isinstance(state.parameters, list) and _tenant_target(state):
        raise TenantScopeError("a bulk UPDATE or DELETE by primary key is not filtered by tenant; it is refused")
    if state.is_update and _tenant_target(state) and _sets_tenant(state.statement):
        raise TenantScopeError("an UPDATE may not move rows to another tenant")
    state.statement = state.statement.options(
        with_loader_criteria(TenantScoped, lambda cls: cls.tenant_id == tenant_id, include_aliases=True))


@event.listens_for(Session, "before_flush")
def _scope_writes(session, flush_context, instances):
    if session.info.get("system"):
        return
    tenant_id = session.info.get("tenant_id")
    for obj in list(session.new) + list(session.dirty) + list(session.deleted):
        if isinstance(obj, TenantScoped):
            if tenant_id is None:
                raise TenantScopeError(f"write of {type(obj).__name__} in a session with no tenant")
            if obj.tenant_id is None and obj not in session.deleted:
                obj.tenant_id = tenant_id
            elif obj.tenant_id != tenant_id:
                raise TenantScopeError(f"{type(obj).__name__} of tenant {obj.tenant_id} written in tenant {tenant_id}")


def tenant_tables() -> list[str]:
    """The tables whose rows belong to one tenant, in dependency order."""
    return [t.name for t in Base.metadata.sorted_tables if "tenant_id" in t.c and t.name != "jobs"]


class Store:
    """On SQLite every transaction begins IMMEDIATE (it takes the write lock at the start), so concurrent workers
    serialise instead of computing a status from a snapshot another worker is about to change. Long work (OCR) is
    done outside transactions; see Engine.prefetch. On Postgres a tenant session takes a transaction-scoped advisory
    lock on its tenant, which serialises status work per tenant while different tenants run in parallel, and sets
    the tenant for row-level security.

    `tenant_url`, on Postgres, connects tenant sessions as a restricted application role (not the owner, no BYPASSRLS):
    with `enable_row_level_security` installed, the database itself refuses other tenants' rows to that role, whatever
    statement reaches it. The owner connection (`url`) creates the schema and runs the system sessions."""

    def __init__(self, url: str = "sqlite:///data/testamur.db", busy_timeout_ms: int = 120_000,
                 tenant_url: str | None = None):
        self.engine = create_engine(url, future=True)
        self.dialect = self.engine.dialect.name
        self.tenant_engine = create_engine(tenant_url, future=True) if tenant_url else self.engine
        if self.dialect == "sqlite":
            @event.listens_for(self.engine, "connect")
            def _pragmas(conn, _):
                conn.isolation_level = None          # let SQLAlchemy's begin event issue BEGIN IMMEDIATE
                cur = conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA foreign_keys=ON")            # as Postgres does: SQLite ignores them by default
                cur.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
                cur.close()

            @event.listens_for(self.engine, "begin")
            def _begin(conn):
                conn.exec_driver_sql("BEGIN IMMEDIATE")

            @event.listens_for(self.engine, "checkin")
            def _checkin(dbapi_conn, _):
                # SQLite keeps a transaction open when its COMMIT is refused (a deferred foreign key), while SQLAlchemy
                # takes the transaction as ended and skips the pool's rollback: end it here, or the connection's next
                # BEGIN fails, and its write lock is held until then
                if dbapi_conn.in_transaction:
                    dbapi_conn.rollback()
        Base.metadata.create_all(self.engine)
        self._factory = sessionmaker(self.engine, expire_on_commit=False)
        self._tenant_factory = sessionmaker(self.tenant_engine, expire_on_commit=False)

    @contextmanager
    def tenant(self, tenant_id: int):
        """A session that can only see and write the given tenant's rows."""
        if tenant_id is None:
            raise TenantScopeError("a tenant session needs a tenant")
        with self._tenant_factory() as s:
            s.info["tenant_id"] = tenant_id
            if self.dialect == "postgresql":
                s.execute(text("SELECT pg_advisory_xact_lock(:t), set_config('testamur.tenant_id', :ts, true)")
                          .execution_options(**{TENANT_SAFE: True}), {"t": tenant_id, "ts": str(tenant_id)})
            yield s
            s.commit()

    @contextmanager
    def system(self):
        """Cross-tenant access, for loading the registry and for measurement. Never used on a request path."""
        with self._factory() as s:
            s.info["system"] = True
            yield s
            s.commit()

    def setting(self, key: str, value: str | None = None) -> str | None:
        """Read a setting, or write it when a value is given."""
        with self.system() as s:
            row = s.get(Setting, key)
            if value is not None:
                if row is None:
                    s.add(Setting(key=key, value=value))
                else:
                    row.value = value
                return value
            return row.value if row else None

    @contextmanager
    def unscoped(self):
        """A session with no tenant at all. Exists so tests can prove it fails closed."""
        with self._factory() as s:
            yield s


def enable_row_level_security(store: Store, role: str) -> None:
    """Postgres only, as the owner: every tenant table admits only rows of the tenant the transaction set (Store.tenant
    sets it), to every statement of `role`, an application role that is neither the owner nor BYPASSRLS. An unset
    tenant matches nothing: it fails closed. Defence in depth under the session's own filter; idempotent."""
    if store.dialect != "postgresql":
        raise RuntimeError("row-level security needs Postgres")
    prep = store.engine.dialect.identifier_preparer
    who = prep.quote(role)
    current = "NULLIF(current_setting('testamur.tenant_id', true), '')::int"
    with store.engine.begin() as conn:
        for name in ["tenants", *tenant_tables()]:
            table, column = prep.quote(name), "id" if name == "tenants" else "tenant_id"
            conn.exec_driver_sql(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            conn.exec_driver_sql(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
            conn.exec_driver_sql(f"CREATE POLICY tenant_isolation ON {table} TO {who} "
                                 f"USING ({column} = {current}) WITH CHECK ({column} = {current})")
            conn.exec_driver_sql(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {who}")
        conn.exec_driver_sql(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {who}")

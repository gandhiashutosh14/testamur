"""Storage, with tenant isolation enforced by the session rather than by remembering to filter.

Every tenant-owned table carries `tenant_id`. A session opened for a tenant gets that tenant's filter added to
every ORM read automatically, and refuses to flush a row that belongs to another tenant. A session opened without a
tenant cannot read tenant data at all: it fails closed. Only the explicit system session (used to load the
registry and by the harness) may read across tenants, and it says so at the call site.

SQLite locally (WAL mode); the same models run on Postgres."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
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


class TenantScopeError(RuntimeError):
    """Raised when tenant data is read or written without the right tenant in scope."""


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class TenantScoped:
    """Mixin: rows that belong to exactly one tenant."""
    tenant_id: Mapped[int] = mapped_column(Integer, index=True)


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))


class BusinessUnit(TenantScoped, Base):
    __tablename__ = "business_units"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))


class Supplier(TenantScoped, Base):
    __tablename__ = "suppliers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    legal_name: Mapped[str] = mapped_column(String(300))
    address: Mapped[str] = mapped_column(String(300), default="")
    company_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    vat_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    lei: Mapped[str | None] = mapped_column(String(40), nullable=True)
    iban: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Engagement(TenantScoped, Base):
    __tablename__ = "engagements"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_unit_id: Mapped[int] = mapped_column(ForeignKey("business_units.id"))
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"))
    tier: Mapped[str] = mapped_column(String(20))
    engagement_type: Mapped[str] = mapped_column(String(40))
    service_category: Mapped[str] = mapped_column(String(60))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)


class Attestation(TenantScoped, Base):
    __tablename__ = "attestations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    supplier_id: Mapped[int] = mapped_column(Integer)
    requirement_id: Mapped[str] = mapped_column(String(60))
    verified_by: Mapped[str] = mapped_column(String(120))
    verified_on: Mapped[date] = mapped_column(Date)
    note: Mapped[str] = mapped_column(Text, default="")


class Blob(TenantScoped, Base):
    """Content-addressed file store. Deduplication is per tenant: sharing across tenants would save a little and
    reveal to one tenant that another holds the same file."""
    __tablename__ = "blobs"
    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    size: Mapped[int] = mapped_column(Integer)
    mime: Mapped[str] = mapped_column(String(60))
    path: Mapped[str] = mapped_column(String(400))
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class UploadRow(TenantScoped, Base):
    __tablename__ = "uploads"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_unit_id: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    filename: Mapped[str] = mapped_column(String(300))
    channel: Mapped[str] = mapped_column(String(40))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime)
    claimed_supplier_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    external_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)


class ExtractionClaim(TenantScoped, Base):
    """A worker reading a file right now. Another worker that needs the same file waits instead of paying for the
    same OCR twice; a claim older than CLAIM_SECONDS (engine.py) is a dead worker's and may be taken over."""
    __tablename__ = "extraction_claims"
    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    claimed_at: Mapped[datetime] = mapped_column(DateTime)


class Document(TenantScoped, Base):
    """One per distinct file (per tenant). Re-uploading the same bytes adds an upload event, not a document."""
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("tenant_id", "sha256"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64))
    doc_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    class_method: Mapped[str] = mapped_column(String(40), default="")
    class_score: Mapped[float] = mapped_column(Float, default=0.0)
    supplier_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
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
    tenant_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    extractor: Mapped[str] = mapped_column(String(60), primary_key=True)
    version: Mapped[str] = mapped_column(String(40), primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    ocr_pages: Mapped[int] = mapped_column(Integer, default=0)
    seconds: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class VerdictRow(TenantScoped, Base):
    __tablename__ = "verdicts"
    __table_args__ = (UniqueConstraint("tenant_id", "engagement_id", "requirement_id", "check_id", "document_id",
                                       "engine_version"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    engagement_id: Mapped[int] = mapped_column(Integer, index=True)
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
    """A person's verdict on one check, for one engagement and document. It replaces the engine's verdict for as
    long as the check still means what it meant when the person decided (its rule digest), across re-runs and engine
    versions. The engine's verdict at the time is kept: comparing the two is per-check accuracy. `kind` says how
    the verdict reached the person: from the review queue (the engine could not decide) or from an audit sample
    (a random draw of the engine's conclusive verdicts, the unbiased basis for accuracy estimates)."""
    __tablename__ = "check_reviews"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    engagement_id: Mapped[int] = mapped_column(Integer, index=True)
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
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class FieldCorrection(TenantScoped, Base):
    """A person's reading of one field, used instead of what extraction read. The rules then judge it like any
    value, so a correction made today is judged by tomorrow's rules too. The machine's reading is kept."""
    __tablename__ = "field_corrections"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(Integer, index=True)
    field: Mapped[str] = mapped_column(String(60))
    raw: Mapped[str] = mapped_column(Text)
    machine_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewer: Mapped[str] = mapped_column(String(300))
    note: Mapped[str] = mapped_column(Text, default="")
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
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(Integer, index=True)
    action: Mapped[str] = mapped_column(String(10))
    doc_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    supplier_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    machine: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    reviewer: Mapped[str] = mapped_column(String(300))
    note: Mapped[str] = mapped_column(Text, default="")
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class RequirementState(TenantScoped, Base):
    __tablename__ = "requirement_states"
    engagement_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    requirement_id: Mapped[str] = mapped_column(String(60), primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(40))
    document_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    via_parent: Mapped[bool] = mapped_column(Boolean, default=False)
    reasons: Mapped[list[Any]] = mapped_column(JSON, default=list)
    failing: Mapped[list[Any]] = mapped_column(JSON, default=list)      # checks that FAIL, for findings that act
    version: Mapped[int] = mapped_column(Integer, default=0)            # one more for every change reported
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
    """Remediation work for a person: chase a supplier for evidence, or follow a finding up. At most one open task
    per engagement and requirement, enforced by the database, not by remembering to check."""
    __tablename__ = "tasks"
    __table_args__ = (Index("one_open_task", "tenant_id", "engagement_id", "requirement_id", unique=True,
                            sqlite_where=text("status = 'open'"), postgresql_where=text("status = 'open'")),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    engagement_id: Mapped[int] = mapped_column(Integer, index=True)
    requirement_id: Mapped[str] = mapped_column(String(60))
    kind: Mapped[str] = mapped_column(String(20))                       # chase_supplier | follow_up
    title: Mapped[str] = mapped_column(Text)
    detail: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(10), default="open")    # open | done | superseded
    opened_at: Mapped[datetime] = mapped_column(DateTime)
    due_at: Mapped[datetime] = mapped_column(DateTime)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    closed_reason: Mapped[str] = mapped_column(Text, default="")
    event_key: Mapped[str] = mapped_column(String(120))


class Notification(TenantScoped, Base):
    """A message to a supplier, kept as written. `key` makes each one unique; a cooldown keeps reminders apart."""
    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("tenant_id", "key"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(160))
    supplier_id: Mapped[int] = mapped_column(Integer)
    engagement_id: Mapped[int] = mapped_column(Integer, index=True)
    requirement_id: Mapped[str] = mapped_column(String(60))
    subject: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Hold(TenantScoped, Base):
    """A purchasing hold on an engagement while a critical requirement is unmet. Placed and released by the engine,
    announced by webhook; reversible by design. At most one open hold per engagement."""
    __tablename__ = "holds"
    __table_args__ = (Index("one_open_hold", "tenant_id", "engagement_id", unique=True,
                            sqlite_where=text("released_at IS NULL"), postgresql_where=text("released_at IS NULL")),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    engagement_id: Mapped[int] = mapped_column(Integer, index=True)
    requirements: Mapped[list[Any]] = mapped_column(JSON, default=list)
    placed_at: Mapped[datetime] = mapped_column(DateTime)
    released_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class WebhookEndpoint(TenantScoped, Base):
    """Where a tenant's own systems (purchasing, ticketing) want to hear about changes; messages are signed with
    the endpoint's secret (Standard Webhooks)."""
    __tablename__ = "webhook_endpoints"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    url: Mapped[str] = mapped_column(String(400))
    secret: Mapped[str] = mapped_column(String(120))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Delivery(TenantScoped, Base):
    """One message to one endpoint, retried with backoff until delivered or dead. `webhook_id` is stable across
    retries, so the receiver can ignore a message it has already processed: delivery is at least once, the effect
    exactly once."""
    __tablename__ = "deliveries"
    __table_args__ = (UniqueConstraint("tenant_id", "endpoint_id", "webhook_id"),)
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
    tenant_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model: Mapped[str] = mapped_column(String(120))
    response: Mapped[str] = mapped_column(Text)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    seconds: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class UsageRow(TenantScoped, Base):
    __tablename__ = "usage"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    day: Mapped[date] = mapped_column(Date)
    provider: Mapped[str] = mapped_column(String(20))     # ocr | model
    units: Mapped[float] = mapped_column(Float)            # OCR pages, or model tokens
    cached: Mapped[bool] = mapped_column(Boolean, default=False)


class TenantBudget(TenantScoped, Base):
    """Per-tenant daily spend caps for the paid steps. None means uncapped. A tenant over its cap waits for the
    next day; its work is deferred, never dropped."""
    __tablename__ = "budgets"
    tenant_id: Mapped[int] = mapped_column(Integer, primary_key=True)
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
    """The tenant's tool catalogue: an agent may call a tool only while the tenant has granted it."""
    __tablename__ = "tool_grants"
    __table_args__ = (UniqueConstraint("tenant_id", "tool"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tool: Mapped[str] = mapped_column(String(60))
    granted_by: Mapped[str] = mapped_column(String(300))
    granted_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Proposal(TenantScoped, Base):
    """A change an agent asked for. Nothing happens until a person confirms it; it is then applied through the same
    engine call a person would make, and audited with both names. Unconfirmed proposals expire."""
    __tablename__ = "proposals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(30))
    args: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    summary: Mapped[str] = mapped_column(Text)
    proposed_by: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(10), default="proposed")    # proposed | applied | declined | expired
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


@event.listens_for(Session, "do_orm_execute")
def _scope_reads(state):
    if not state.is_select or state.execution_options.get("all_tenants") or state.session.info.get("system"):
        return
    tenant_id = state.session.info.get("tenant_id")
    if tenant_id is None:
        raise TenantScopeError("tenant data read in a session with no tenant")
    state.statement = state.statement.options(
        with_loader_criteria(TenantScoped, lambda cls: cls.tenant_id == tenant_id, include_aliases=True))


@event.listens_for(Session, "before_flush")
def _scope_writes(session, flush_context, instances):
    if session.info.get("system"):
        return
    tenant_id = session.info.get("tenant_id")
    for obj in list(session.new) + list(session.dirty):
        if isinstance(obj, TenantScoped):
            if tenant_id is None:
                raise TenantScopeError(f"write of {type(obj).__name__} in a session with no tenant")
            if obj.tenant_id is None:
                obj.tenant_id = tenant_id
            elif obj.tenant_id != tenant_id:
                raise TenantScopeError(f"{type(obj).__name__} of tenant {obj.tenant_id} written in tenant {tenant_id}")


class Store:
    """On SQLite every transaction begins IMMEDIATE (it takes the write lock at the start), so concurrent workers
    serialise instead of computing a status from a snapshot another worker is about to change. Long work (OCR) is
    done outside transactions; see Engine.prefetch. On Postgres a tenant session takes a transaction-scoped advisory
    lock on its tenant, which serialises status work per tenant while different tenants run in parallel."""

    def __init__(self, url: str = "sqlite:///data/testamur.db", busy_timeout_ms: int = 120_000):
        self.engine = create_engine(url, future=True)
        self.dialect = self.engine.dialect.name
        if self.dialect == "sqlite":
            @event.listens_for(self.engine, "connect")
            def _pragmas(conn, _):
                conn.isolation_level = None          # let SQLAlchemy's begin event issue BEGIN IMMEDIATE
                cur = conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
                cur.close()

            @event.listens_for(self.engine, "begin")
            def _begin(conn):
                conn.exec_driver_sql("BEGIN IMMEDIATE")
        Base.metadata.create_all(self.engine)
        self._factory = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def tenant(self, tenant_id: int):
        """A session that can only see and write the given tenant's rows."""
        if tenant_id is None:
            raise TenantScopeError("a tenant session needs a tenant")
        with self._factory() as s:
            s.info["tenant_id"] = tenant_id
            if self.dialect == "postgresql":
                from sqlalchemy import text
                s.execute(text("SELECT pg_advisory_xact_lock(:t)"), {"t": tenant_id})
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

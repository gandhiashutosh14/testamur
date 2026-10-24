"""A durable job queue in the same SQL database: competing workers, leases, retries, deferral and a dead-letter
state.

- **Lease, not delete.** A worker claims a job by setting a lease that expires (a visibility timeout). If the worker
  dies, the lease runs out and another worker takes the job. Processing is idempotent (content-addressed documents,
  upserted verdicts), so doing a job twice is safe.
- **Fenced.** Every lease carries a fresh token. Completing, failing, deferring or renewing a job needs the token of
  the lease that holds it, checked in the same UPDATE: a worker that stalled past its lease and comes back cannot
  mark done a job another worker now holds (it gets LeaseLost). Long work renews the lease as it goes (a heartbeat).
- **Atomic claim.** One UPDATE ... RETURNING picks and leases a job in a single statement; on Postgres the candidate
  row is selected FOR UPDATE SKIP LOCKED so workers never wait on each other.
- **Retry with backoff, then dead-letter.** A failing job is retried later; after `max_attempts` it is parked as
  `dead` with its last error, for a person, instead of blocking the queue forever. So is a job whose lease ran out
  `max_attempts` times: a job that kills every worker that takes it must not take them all down in turn.
- **Defer without penalty.** A job stopped by a tenant's spend gate is deferred to when the budget resets; that does
  not count as an attempt. Work waits, it is never dropped.
- **Idempotent enqueue.** A job has a key unique within its tenant; enqueueing the same key twice is a no-op.

Jobs carry their tenant explicitly. A worker opens a tenant-scoped session from the job, never from ambient state.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import JSON, DateTime, Integer, String, Text, UniqueConstraint, and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column

from .store import Base, Store, utcnow


class LeaseLost(RuntimeError):
    """The worker no longer holds the job: its lease ran out and the job was leased again (or finished)."""

    def __init__(self, job_id: int):
        super().__init__(f"job {job_id} is no longer held by this lease")
        self.job_id = job_id


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("tenant_id", "key"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, index=True)
    kind: Mapped[str] = mapped_column(String(40))
    key: Mapped[str] = mapped_column(String(200))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(12), default="queued", index=True)   # queued|leased|done|dead
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    available_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    leased_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(40), nullable=True)
    deferrals: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


@dataclass
class Leased:
    id: int
    tenant_id: int
    kind: str
    payload: dict[str, Any]
    attempts: int
    token: str                      # the fence: needed to complete, fail, defer or renew this lease


def enqueue(store: Store, tenant_id: int, kind: str, key: str, payload: dict[str, Any] | None = None,
            max_attempts: int = 5, now: datetime | None = None) -> bool:
    """Returns False if a job with this key already exists for the tenant."""
    try:
        with store.system() as s:
            s.add(Job(tenant_id=tenant_id, kind=kind, key=key, payload=payload or {}, max_attempts=max_attempts,
                      available_at=now or utcnow(), created_at=now or utcnow()))
        return True
    except IntegrityError:
        return False


def lease(store: Store, worker: str, lease_seconds: float = 120.0, now: datetime | None = None) -> Leased | None:
    now = now or utcnow()
    expired = and_(Job.status == "leased", Job.lease_until < now)
    claimable = or_(and_(Job.status == "queued", Job.available_at <= now), and_(expired, Job.attempts < Job.max_attempts))
    candidate = select(Job.id).where(claimable).order_by(Job.available_at, Job.id).limit(1)
    token = uuid.uuid4().hex
    with store.system() as s:
        # a job whose lease ran out on every attempt is parked for a person, not handed to the next worker
        s.execute(update(Job).where(expired, Job.attempts >= Job.max_attempts).values(
            status="dead", finished_at=now, lease_until=None, lease_token=None,
            last_error="the lease ran out on every attempt: the job may be killing its workers"))
        if s.bind.dialect.name == "postgresql":
            candidate = candidate.with_for_update(skip_locked=True)
        stmt = (update(Job).where(Job.id == candidate.scalar_subquery(), claimable)
                .values(status="leased", lease_until=now + timedelta(seconds=lease_seconds), leased_by=worker,
                        lease_token=token, attempts=Job.attempts + 1, started_at=func.coalesce(Job.started_at, now))
                .returning(Job.id, Job.tenant_id, Job.kind, Job.payload, Job.attempts, Job.lease_token))
        row = s.execute(stmt).first()
        return Leased(*row) if row else None


def _held(job_id: int, token: str):
    return and_(Job.id == job_id, Job.lease_token == token, Job.status == "leased")


def _fenced(s, stmt, job_id: int) -> None:
    if s.execute(stmt).rowcount != 1:
        raise LeaseLost(job_id)


def renew(store: Store, job_id: int, token: str, lease_seconds: float, now: datetime | None = None) -> None:
    """Extend the lease the worker holds (a heartbeat); LeaseLost if it no longer holds it."""
    with store.system() as s:
        _fenced(s, update(Job).where(_held(job_id, token)).values(
            lease_until=(now or utcnow()) + timedelta(seconds=lease_seconds)), job_id)


def complete(store: Store, job_id: int, token: str, now: datetime | None = None) -> None:
    with store.system() as s:
        _fenced(s, update(Job).where(_held(job_id, token)).values(
            status="done", finished_at=now or utcnow(), lease_until=None, lease_token=None), job_id)


def defer(store: Store, job_id: int, token: str, until: datetime, reason: str) -> None:
    """Hold a job until `until` without counting an attempt (a spend gate, not a failure)."""
    with store.system() as s:
        _fenced(s, update(Job).where(_held(job_id, token)).values(
            status="queued", available_at=until, lease_until=None, lease_token=None, attempts=Job.attempts - 1,
            deferrals=Job.deferrals + 1, last_error=reason), job_id)


def fail(store: Store, job_id: int, token: str, error: str, now: datetime | None = None,
         base_delay: float = 5.0) -> str:
    """Retry later with exponential backoff, or dead-letter after max_attempts. Returns the new status."""
    now = now or utcnow()
    with store.system() as s:
        held = select(Job).where(_held(job_id, token))
        job = s.scalar(held.with_for_update() if s.bind.dialect.name == "postgresql" else held)
        if job is None:
            raise LeaseLost(job_id)
        job.lease_until, job.lease_token, job.last_error = None, None, error
        if job.attempts >= job.max_attempts:
            job.status, job.finished_at = "dead", now
            return "dead"
        job.status = "queued"
        job.available_at = now + timedelta(seconds=base_delay * 2 ** (job.attempts - 1))
        return "queued"


def counts(store: Store) -> dict[str, int]:
    with store.system() as s:
        return dict(s.execute(select(Job.status, func.count()).group_by(Job.status)).all())


def next_available(store: Store) -> datetime | None:
    with store.system() as s:
        return s.scalar(select(func.min(Job.available_at)).where(Job.status == "queued"))

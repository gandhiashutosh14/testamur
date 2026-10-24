"""A worker: lease a job, do it, record the outcome. Run several as separate processes; they compete for jobs through
the queue and need nothing else in common."""
from __future__ import annotations

import json
import os
import threading
import time
from collections import Counter
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

from . import jobs
from .budget import BudgetExceeded
from .engine import Engine, ReadingElsewhere
from .extract import Extractor, RapidOcr
from .modeltier import CassetteMiss
from .pack import load_pack
from .store import Store, utcnow
from .telemetry import event, span

SHORT_WAIT = 30.0           # seconds a worker waits for a job that becomes available soon, instead of exiting
IDLE_POLL = 1.0             # seconds a serving worker sleeps on an empty queue


class FileClock:
    """Wall clock plus an offset read from a file, so a simulation can jump a day ahead (to release jobs a spend gate
    deferred) while every worker process agrees on the time."""

    def __init__(self, path: str | Path | None):
        self.path = Path(path) if path else None

    def __call__(self) -> datetime:
        offset = 0.0
        if self.path and self.path.exists():
            offset = json.loads(self.path.read_text(encoding="utf-8")).get("offset_seconds", 0.0)
        return utcnow() + timedelta(seconds=offset)

    def advance(self, seconds: float) -> None:
        current = json.loads(self.path.read_text(encoding="utf-8")).get("offset_seconds", 0.0) if self.path.exists() else 0.0
        self.path.write_text(json.dumps({"offset_seconds": current + seconds}), encoding="utf-8")


def run_worker(db_url: str, blob_root: str, as_of: date, worker_id: str, *, lease_seconds: float = 120.0,
               clock_file: str | None = None, ocr_threads: int | None = None, crash_after: int | None = None,
               model=None) -> dict:
    """Process jobs until none is claimable soon. `crash_after=k` kills the process abruptly after leasing its
    k-th job, as a power cut would: the lease is left to expire and another worker must finish the job."""
    store = Store(db_url)
    clock = FileClock(clock_file)
    engine = Engine(store, load_pack(), as_of, blob_root, extractor=Extractor(RapidOcr(threads=ocr_threads)),
                    model=model, clock=clock)
    return work(engine, worker_id, lease_seconds=lease_seconds, crash_after=crash_after)


def work(engine: Engine, worker_id: str, *, investigations=None, lease_seconds: float = 120.0,
         crash_after: int | None = None, forever: bool = False, stop: threading.Event | None = None) -> dict:
    """The worker loop over one engine. `investigations` (testamur.investigate.Investigations) runs investigation
    jobs; `forever` keeps polling an empty queue, as the service's worker does, until `stop` is set."""
    store, clock = engine.store, engine.clock
    stats: Counter = Counter()
    leased = 0
    while not (stop is not None and stop.is_set()):
        job = jobs.lease(store, worker_id, lease_seconds, now=clock())
        if job is None:
            soon = jobs.next_available(store)          # a short wait (a file another worker is reading, a retry)
            if soon is not None and (soon - clock()).total_seconds() <= SHORT_WAIT:
                time.sleep(max(0.2, (soon - clock()).total_seconds()))
                continue
            if forever:
                (stop.wait if stop is not None else time.sleep)(IDLE_POLL)
                continue
            break                                       # nothing soon: a spend cap's next day, or an empty queue
        leased += 1
        if crash_after is not None and leased == crash_after:
            os._exit(17)

        def heartbeat(job=job) -> None:              # renewed with every OCR page, so a long scan keeps its lease
            jobs.renew(store, job.id, job.token, lease_seconds, now=clock())
        try:
            try:
                with span("job", job_id=job.id, tenant_id=job.tenant_id, kind=job.kind, attempt=job.attempts):
                    if job.kind == "process_upload":
                        stats["ocr_pages"] += engine.prefetch(job.tenant_id, job.payload["upload_id"],
                                                              heartbeat=heartbeat)
                        engine.process(job.tenant_id, job.payload["upload_id"])
                    elif job.kind == "refresh":
                        engine.refresh(job.tenant_id)
                    elif job.kind == "investigate" and investigations is not None:
                        with _kept(store, job, lease_seconds, clock) as lost:
                            investigations.execute(job.tenant_id, job.payload["run_id"], stop=lost.is_set)
                        if lost.is_set():
                            raise jobs.LeaseLost(f"job {job.id} was taken over while its run was executing")
                    else:
                        raise ValueError(f"unknown job kind {job.kind}")
                jobs.complete(store, job.id, job.token, now=clock())
                event("job done", job_id=job.id, tenant_id=job.tenant_id, kind=job.kind)
                stats["done"] += 1
            except BudgetExceeded as b:
                jobs.defer(store, job.id, job.token, b.retry_at, str(b))
                stats["deferred"] += 1
            except ReadingElsewhere as r:
                jobs.defer(store, job.id, job.token, r.retry_at, str(r))
                stats["waited"] += 1
            except (CassetteMiss, jobs.LeaseLost):
                raise
            except Exception as exc:  # noqa: BLE001 - every failure is recorded on the job; repeated ones dead-letter
                stats[jobs.fail(store, job.id, job.token, f"{type(exc).__name__}: {exc}", now=clock())] += 1
        except jobs.LeaseLost:
            # this worker stalled past its lease and another took the job over: its outcome is the other worker's
            # to record (the work is idempotent), and nothing this worker says about the job counts any more
            stats["lease_lost"] += 1
    return dict(stats)


@contextmanager
def _kept(store: Store, job, lease_seconds: float, clock):
    """Keep a long job's lease (an investigation waits on a model for minutes) renewed from a thread, at a third of
    the lease. Yields an event set if the lease was lost: another worker owns the job, and this one must stop."""
    lost, done = threading.Event(), threading.Event()

    def beat() -> None:
        while not done.wait(lease_seconds / 3):
            try:
                jobs.renew(store, job.id, job.token, lease_seconds, now=clock())
            except jobs.LeaseLost:
                lost.set()
                return
    t = threading.Thread(target=beat, name=f"lease-{job.id}", daemon=True)
    t.start()
    try:
        yield lost
    finally:
        done.set()
        t.join()


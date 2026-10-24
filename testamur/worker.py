"""A worker: lease a job, do it, record the outcome. Run several as separate processes; they compete for jobs through
the queue and need nothing else in common."""
from __future__ import annotations

import json
import os
import time
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

from . import jobs
from .budget import BudgetExceeded
from .engine import Engine, ReadingElsewhere
from .extract import Extractor, RapidOcr
from .modeltier import CassetteMiss
from .pack import load_pack
from .store import Store, utcnow

SHORT_WAIT = 30.0           # seconds a worker waits for a job that becomes available soon, instead of exiting


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
    stats: Counter = Counter()
    leased = 0
    while True:
        job = jobs.lease(store, worker_id, lease_seconds, now=clock())
        if job is None:
            soon = jobs.next_available(store)          # a short wait (a file another worker is reading, a retry)
            if soon is not None and (soon - clock()).total_seconds() <= SHORT_WAIT:
                time.sleep(max(0.2, (soon - clock()).total_seconds()))
                continue
            break                                       # nothing soon: a spend cap's next day, or an empty queue
        leased += 1
        if crash_after is not None and leased == crash_after:
            os._exit(17)

        def heartbeat(job=job) -> None:              # renewed with every OCR page, so a long scan keeps its lease
            jobs.renew(store, job.id, job.token, lease_seconds, now=clock())
        try:
            try:
                if job.kind == "process_upload":
                    stats["ocr_pages"] += engine.prefetch(job.tenant_id, job.payload["upload_id"], heartbeat=heartbeat)
                    engine.process(job.tenant_id, job.payload["upload_id"])
                elif job.kind == "refresh":
                    engine.refresh(job.tenant_id)
                else:
                    raise ValueError(f"unknown job kind {job.kind}")
                jobs.complete(store, job.id, job.token, now=clock())
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


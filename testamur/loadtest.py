"""Burst load test: every upload of a world lands at once, workers drain the queue, and the run is measured.

Three scenarios, each against a fresh database:
1. **Scaling.** 1, 2, ... workers (separate processes, OCR threads split between them). Throughput, job latency,
   OCR pages paid, and whether the final statuses are identical to the single-worker run.
2. **Spend gate.** One tenant gets a daily OCR cap well below its burst. Jobs over the cap are deferred to the next
   day, the clock is advanced, and the run continues until everything is done: how many were deferred, how many days
   it took, and that nothing was dropped.
3. **Crash.** One worker dies abruptly mid-job. Its lease expires, another worker finishes the job, and the final
   statuses must be identical to the uninterrupted run.
"""
from __future__ import annotations

import json
import multiprocessing as mp
import os
import shutil
import statistics
import time
from datetime import timedelta
from pathlib import Path

from sqlalchemy import func, select

from . import jobs
from .engine import Engine
from .harness import git_stamp, load_registry
from .pack import load_pack
from .store import ExtractionRow, RequirementState, Store, TenantBudget, UsageRow
from .synth.world import load_world
from .worker import FileClock, run_worker


def _fresh(path: Path) -> tuple[Store, Path]:
    for suffix in ("", "-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)
    blobs = path.parent / (path.stem + "_blobs")
    shutil.rmtree(blobs, ignore_errors=True)
    return Store(f"sqlite:///{path}"), blobs


def _enqueue_world(store: Store, world, world_dir: Path, blobs: Path) -> int:
    load_registry(store, world)
    engine = Engine(store, load_pack(), world.as_of, blobs)
    for u in world.uploads:
        uid = engine.ingest(u.tenant_id, u.business_unit_id, u.filename, (world_dir / u.file).read_bytes(), u.channel,
                            u.uploaded_at, u.claimed_supplier_id, external_ref=f"world:{u.id}")
        jobs.enqueue(store, u.tenant_id, "process_upload", key=f"upload:{uid}", payload={"upload_id": uid})
    return len(world.uploads)


def _drain(db: Path, blobs: Path, as_of, n_workers: int, clock_file: Path, lease_seconds: float,
           crash_first_after: int | None = None) -> list[int]:
    ctx = mp.get_context("spawn")
    threads = max(1, (os.cpu_count() or 2) // n_workers)
    procs = []
    for i in range(n_workers):
        kwargs = {"db_url": f"sqlite:///{db}", "blob_root": str(blobs), "as_of": as_of, "worker_id": f"w{i}",
                  "lease_seconds": lease_seconds, "clock_file": str(clock_file), "ocr_threads": threads,
                  "crash_after": crash_first_after if i == 0 else None}
        p = ctx.Process(target=run_worker, kwargs=kwargs)
        p.start()
        procs.append(p)
    for p in procs:
        p.join()
    return [p.exitcode for p in procs]


def _statuses(store: Store, world, engine: Engine) -> dict:
    for t in world.tenants:
        engine.refresh(t.id)
    with store.system() as s:
        return {(r.engagement_id, r.requirement_id): r.status for r in s.scalars(select(RequirementState))}


def _metrics(store: Store) -> dict:
    with store.system() as s:
        rows = s.scalars(select(jobs.Job)).all()
        ocr = s.scalar(select(func.coalesce(func.sum(UsageRow.units), 0)).where(UsageRow.provider == "ocr")) or 0
        ocr_secs = s.scalar(select(func.coalesce(func.sum(ExtractionRow.seconds), 0.0)).where(
            ExtractionRow.ocr_pages > 0)) or 0.0
    done = [r for r in rows if r.status == "done"]
    lat = sorted((r.finished_at - r.created_at).total_seconds() for r in done if r.finished_at)
    return {"jobs": len(rows), "done": len(done), "dead": sum(r.status == "dead" for r in rows),
            "retried": sum(r.attempts > 1 for r in rows), "deferrals": sum(r.deferrals for r in rows),
            "ocr_pages": int(ocr), "ocr_seconds": round(ocr_secs, 1),
            "latency_p50": round(statistics.median(lat), 1) if lat else None,
            "latency_p95": round(lat[int(0.95 * (len(lat) - 1))], 1) if lat else None}


def run(world_dir: str | Path, out_dir: str | Path, workers: list[int], lease_seconds: float = 20.0,
        budget_pages: int | None = None, crash: bool = True) -> dict:
    world_dir, out_dir = Path(world_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    world = load_world(world_dir)
    pack = load_pack()
    results = {"scaling": [], "budget": None, "crash": None}
    reference = None
    for n in workers:
        db = out_dir / f"load_{n}.db"
        store, blobs = _fresh(db)
        clock = out_dir / f"clock_{n}.json"
        clock.unlink(missing_ok=True)
        uploads = _enqueue_world(store, world, world_dir, blobs)
        t0 = time.perf_counter()
        codes = _drain(db, blobs, world.as_of, n, clock, lease_seconds)
        wall = time.perf_counter() - t0
        m = _metrics(store)
        statuses = _statuses(store, world, Engine(store, pack, world.as_of, blobs))
        reference = reference or statuses
        results["scaling"].append({"workers": n, "uploads": uploads, "seconds": round(wall, 1),
                                   "uploads_per_minute": round(60 * uploads / wall, 1),
                                   "ocr_pages_per_minute": round(60 * m["ocr_pages"] / wall, 1), **m,
                                   "exit_codes": codes, "statuses_identical_to_first": statuses == reference})
        print(f"workers={n}: {wall:.0f}s {m}", flush=True)

    if budget_pages is not None:
        db = out_dir / "load_budget.db"
        store, blobs = _fresh(db)
        clock = out_dir / "clock_budget.json"
        clock.unlink(missing_ok=True)
        uploads = _enqueue_world(store, world, world_dir, blobs)
        with store.system() as s:
            s.add(TenantBudget(tenant_id=1, ocr_pages_per_day=budget_pages))
        days, deferred_first_day = 0, None
        while True:
            _drain(db, blobs, world.as_of, 2, clock, lease_seconds)
            counts = jobs.counts(store)
            if deferred_first_day is None:
                deferred_first_day = counts.get("queued", 0)
            if not counts.get("queued") and not counts.get("leased"):
                break
            FileClock(clock).advance(timedelta(days=1).total_seconds())
            days += 1
            if days > 60:
                break
        m = _metrics(store)
        with store.system() as s:
            per_day = s.execute(select(UsageRow.day, func.sum(UsageRow.units)).where(
                UsageRow.provider == "ocr", UsageRow.tenant_id == 1).group_by(UsageRow.day)).all()
        statuses = _statuses(store, world, Engine(store, pack, world.as_of, blobs))
        results["budget"] = {"tenant": 1, "ocr_pages_per_day": budget_pages, "uploads": uploads,
                             "jobs_waiting_after_first_day": deferred_first_day, "extra_days": days,
                             "max_pages_any_day": int(max((u for _, u in per_day), default=0)),
                             "statuses_identical_to_unbudgeted": statuses == reference, **m}
        print("budget:", results["budget"], flush=True)

    if crash:
        db = out_dir / "load_crash.db"
        store, blobs = _fresh(db)
        clock = out_dir / "clock_crash.json"
        clock.unlink(missing_ok=True)
        _enqueue_world(store, world, world_dir, blobs)
        codes = _drain(db, blobs, world.as_of, 2, clock, lease_seconds, crash_first_after=3)
        time.sleep(lease_seconds + 1)                       # let the dead worker's lease expire
        codes2 = _drain(db, blobs, world.as_of, 2, clock, lease_seconds)
        m = _metrics(store)
        statuses = _statuses(store, world, Engine(store, pack, world.as_of, blobs))
        results["crash"] = {"exit_codes_first_pass": codes, "exit_codes_second_pass": codes2,
                            "statuses_identical_to_uninterrupted": statuses == reference, **m}
        print("crash:", results["crash"], flush=True)
    results["meta"] = {"commit": git_stamp(Path.cwd()), "world_seed": world.seed, "world_size": world.size,
                       "cpu_count": os.cpu_count(), "lease_seconds": lease_seconds}
    return results


def render(r: dict, command: str) -> str:
    meta = r["meta"]
    out = [f"# Load test: {meta['world_size']} world, seed {meta['world_seed']}", "",
           f"Commit `{meta['commit']}`, command `{command}`, {meta['cpu_count']} logical CPUs on the author's laptop. "
           "Every upload is enqueued at once; worker processes drain the queue; the clause model is off (OCR is the "
           "cost being measured).", "", "## Scaling", "",
           "| Workers | Uploads | Wall time | Uploads/min | OCR pages | OCR pages/min | Job latency p50 / p95 | Dead | Statuses identical to 1 worker |",
           "|---|---|---|---|---|---|---|---|---|"]
    for x in r["scaling"]:
        out.append(f"| {x['workers']} | {x['uploads']} | {x['seconds']} s | {x['uploads_per_minute']} | "
                   f"{x['ocr_pages']} | {x['ocr_pages_per_minute']} | {x['latency_p50']} s / {x['latency_p95']} s | "
                   f"{x['dead']} | {'yes' if x['statuses_identical_to_first'] else '**no**'} |")
    if r.get("budget"):
        b = r["budget"]
        out += ["", "## Spend gate", "",
                f"Tenant {b['tenant']} capped at {b['ocr_pages_per_day']} OCR pages a day. After the first day "
                f"{b['jobs_waiting_after_first_day']} jobs were waiting (deferred, not failed); the clock was advanced a "
                f"day at a time and the queue drained after {b['extra_days']} extra days. The most pages paid on any "
                f"day: {b['max_pages_any_day']}. Jobs done: {b['done']} of {b['jobs']}; dead: {b['dead']}. Final statuses "
                f"identical to the run without a cap: {'yes' if b['statuses_identical_to_unbudgeted'] else '**no**'}."]
    if r.get("crash"):
        c = r["crash"]
        out += ["", "## Crash recovery", "",
                f"Worker 0 was killed abruptly after leasing its third job (exit codes {c['exit_codes_first_pass']}). "
                f"After its lease expired, a second pass finished the queue: {c['done']} of {c['jobs']} jobs done, "
                f"{c['retried']} job(s) taken over after the crash, {c['dead']} dead. Final statuses identical to the "
                f"uninterrupted run: {'yes' if c['statuses_identical_to_uninterrupted'] else '**no**'}."]
    out += ["", "## What this does not show", "",
            "- One laptop, SQLite: the database serialises the short bookkeeping transactions; OCR, the expensive part, "
            "runs outside them. A Postgres deployment would lock per tenant instead.",
            "- Timings are this machine's, not a benchmark.", ""]
    return "\n".join(out)


def write(r: dict, path: str | Path, command: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.with_suffix(".json").write_text(json.dumps(r, indent=2, default=str), encoding="utf-8")
    path.write_text(render(r, command), encoding="utf-8")

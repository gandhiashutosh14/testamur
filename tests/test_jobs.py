"""The queue and the spend gates: leases expire, failures back off then dead-letter, deferral is not failure, and a
tenant over budget pays for nothing."""
import random
from datetime import datetime, timedelta

import pytest

from testamur import jobs
from testamur.budget import BudgetExceeded
from testamur.engine import LOCK_SECONDS, Engine, ReadingElsewhere
from testamur.pack import load_pack
from testamur.store import ExtractionRow, Matter, ReadingLock, Store, Tenant, TenantBudget, UsageRow
from testamur.synth.render import DocContent, render_pdf, to_scan_pdf

T0 = datetime(2026, 10, 1, 9, 0)


@pytest.fixture()
def store(db_url):
    return Store(db_url)


def test_enqueue_is_idempotent_per_tenant(store):
    assert jobs.enqueue(store, 1, "process_upload", "upload:1", {"upload_id": 1}, now=T0)
    assert not jobs.enqueue(store, 1, "process_upload", "upload:1", {"upload_id": 1}, now=T0)
    assert jobs.enqueue(store, 2, "process_upload", "upload:1", {"upload_id": 1}, now=T0)   # another tenant's key


def test_a_lease_is_exclusive_until_it_expires(store):
    jobs.enqueue(store, 1, "process_upload", "a", now=T0)
    first = jobs.lease(store, "w1", lease_seconds=60, now=T0)
    assert first is not None and first.attempts == 1
    assert jobs.lease(store, "w2", lease_seconds=60, now=T0 + timedelta(seconds=30)) is None
    again = jobs.lease(store, "w2", lease_seconds=60, now=T0 + timedelta(seconds=61))     # w1 died
    assert again is not None and again.id == first.id and again.attempts == 2


def test_failures_back_off_then_dead_letter(store):
    jobs.enqueue(store, 1, "process_upload", "a", max_attempts=2, now=T0)
    j = jobs.lease(store, "w", now=T0)
    assert jobs.fail(store, j.id, j.token, "boom", now=T0) == "queued"
    assert jobs.lease(store, "w", now=T0) is None                       # backing off
    j = jobs.lease(store, "w", now=T0 + timedelta(seconds=10))
    assert jobs.fail(store, j.id, j.token, "boom again", now=T0) == "dead"
    assert jobs.counts(store) == {"dead": 1}


def test_a_deferral_is_not_an_attempt(store):
    jobs.enqueue(store, 1, "process_upload", "a", max_attempts=1, now=T0)
    j = jobs.lease(store, "w", now=T0)
    jobs.defer(store, j.id, j.token, T0 + timedelta(days=1), "over budget")
    assert jobs.lease(store, "w", now=T0 + timedelta(hours=1)) is None
    j = jobs.lease(store, "w", now=T0 + timedelta(days=1))
    assert j is not None and j.attempts == 1                            # still its first real attempt


def test_a_worker_whose_lease_ran_out_cannot_finish_the_job_another_worker_holds(store):
    """Worker A stalls past its lease; B takes the job. A's late completion, failure, deferral or heartbeat must not
    touch B's lease: the token fences it."""
    jobs.enqueue(store, 1, "process_upload", "a", now=T0)
    a = jobs.lease(store, "A", lease_seconds=60, now=T0)
    b = jobs.lease(store, "B", lease_seconds=60, now=T0 + timedelta(seconds=61))
    assert b.id == a.id and b.token != a.token
    late = T0 + timedelta(seconds=70)
    for stale in (lambda: jobs.complete(store, a.id, a.token, now=late),
                  lambda: jobs.fail(store, a.id, a.token, "late", now=late),
                  lambda: jobs.defer(store, a.id, a.token, late, "late"),
                  lambda: jobs.renew(store, a.id, a.token, 60, now=late)):
        with pytest.raises(jobs.LeaseLost):
            stale()
    assert jobs.counts(store) == {"leased": 1}                          # still B's
    jobs.complete(store, b.id, b.token, now=late)
    assert jobs.counts(store) == {"done": 1}
    with pytest.raises(jobs.LeaseLost):                                  # and once done, done
        jobs.complete(store, b.id, b.token, now=late)


def test_a_heartbeat_keeps_a_long_job_from_being_taken_over(store):
    jobs.enqueue(store, 1, "process_upload", "a", now=T0)
    a = jobs.lease(store, "A", lease_seconds=60, now=T0)
    jobs.renew(store, a.id, a.token, 60, now=T0 + timedelta(seconds=50))     # still reading, page after page
    assert jobs.lease(store, "B", now=T0 + timedelta(seconds=90)) is None   # the original lease would have run out
    jobs.complete(store, a.id, a.token, now=T0 + timedelta(seconds=95))


def test_a_job_whose_lease_keeps_running_out_is_dead_lettered(store):
    """A job that kills every worker that takes it is parked for a person after max_attempts, not retried forever."""
    jobs.enqueue(store, 1, "process_upload", "a", max_attempts=2, now=T0)
    t = T0
    for _ in range(2):
        assert jobs.lease(store, "w", lease_seconds=60, now=t) is not None   # the worker dies holding it
        t += timedelta(seconds=61)
    assert jobs.lease(store, "w", lease_seconds=60, now=t) is None
    assert jobs.counts(store) == {"dead": 1}


def test_a_tenant_over_its_ocr_budget_pays_for_nothing(store, tmp_path):
    with store.system() as s:
        s.add_all([Tenant(id=1, name="T"), Matter(id=1, tenant_id=1, name="U", commencement=T0.date(), announced=T0.date(), claims_due=T0.date()),
                   TenantBudget(tenant_id=1, ocr_pages_per_day=0)])
    rng = random.Random(1)
    scan = to_scan_pdf(render_pdf(DocContent([[("title", "LETTER OF AUTHORITY"), ("kv", "Financial creditor", "X")]]), rng), rng)
    engine = Engine(store, load_pack(), T0.date(), tmp_path / "blobs", clock=lambda: T0)
    uid = engine.ingest(1, 1, "scan.pdf", scan, "staff_upload", T0)
    with pytest.raises(BudgetExceeded) as exc:
        engine.prefetch(1, uid)
    assert exc.value.retry_at == datetime(2026, 10, 2)
    with store.tenant(1) as s:
        assert s.query(UsageRow).count() == 0                            # refused before any page was read
    born_digital = render_pdf(DocContent([[("title", "LETTER OF AUTHORITY"), ("kv", "Financial creditor", "X"),
                                           ("kv", "Valid until", "31.03.2027")]]), rng)
    engine.prefetch(1, engine.ingest(1, 1, "digital.pdf", born_digital, "staff_upload", T0))   # no OCR: allowed


def test_two_workers_cannot_both_spend_the_last_of_a_budget(store):
    from testamur import budget
    with store.system() as s:
        s.add_all([Tenant(id=1, name="T"), TenantBudget(tenant_id=1, ocr_pages_per_day=3)])
    with store.tenant(1) as s:                       # worker A reserves 2 of 3 pages and starts its OCR
        budget.reserve(s, 1, "ocr", 2, T0.date())
    with pytest.raises(BudgetExceeded), store.tenant(1) as s:   # worker B, before A has finished, wants 2 more
        budget.reserve(s, 1, "ocr", 2, T0.date())
    with store.tenant(1) as s:
        budget.reserve(s, 1, "ocr", 1, T0.date())   # what is left can still be spent


def test_a_document_larger_than_a_days_cap_runs_alone_on_a_fresh_day(store):
    """Otherwise it would be deferred forever; spend stays bounded by the larger of the cap and one document."""
    from testamur import budget
    with store.system() as s:
        s.add_all([Tenant(id=1, name="T"), TenantBudget(tenant_id=1, ocr_pages_per_day=3)])
    with store.tenant(1) as s:
        budget.reserve(s, 1, "ocr", 1, T0.date())
    with pytest.raises(BudgetExceeded), store.tenant(1) as s:     # not on a day something else was spent
        budget.reserve(s, 1, "ocr", 5, T0.date())
    tomorrow = T0.date() + timedelta(days=1)
    with store.tenant(1) as s:
        budget.reserve(s, 1, "ocr", 5, tomorrow)                     # alone on a fresh day
    with pytest.raises(BudgetExceeded), store.tenant(1) as s:     # and nothing else that day
        budget.reserve(s, 1, "ocr", 1, tomorrow)


def test_a_file_one_worker_is_reading_is_not_paid_for_again_by_another(store, tmp_path):
    import hashlib
    with store.system() as s:
        s.add_all([Tenant(id=1, name="T"), Matter(id=1, tenant_id=1, name="U", commencement=T0.date(), announced=T0.date(), claims_due=T0.date())])
    rng = random.Random(2)
    scan = to_scan_pdf(render_pdf(DocContent([[("title", "LETTER OF AUTHORITY"), ("kv", "Financial creditor", "X")]]), rng), rng)
    now = [T0]
    engine = Engine(store, load_pack(), T0.date(), tmp_path / "blobs", clock=lambda: now[0])
    first = engine.ingest(1, 1, "a.pdf", scan, "staff_upload", T0)
    second = engine.ingest(1, 1, "b.pdf", scan, "email_ingest", T0)
    with store.tenant(1) as s:                                       # worker A has claimed it and is reading it
        s.add(ReadingLock(sha256=hashlib.sha256(scan).hexdigest(), tenant_id=1, locked_at=T0))
    with pytest.raises(ReadingElsewhere):
        engine.prefetch(1, second)                                    # worker B waits instead of paying again
    now[0] = T0 + timedelta(seconds=LOCK_SECONDS + 1)                # worker A died; its lock went stale
    assert engine.prefetch(1, second) > 0                            # taken over and read once
    assert engine.prefetch(1, first) == 0                            # and never again
    with store.tenant(1) as s:
        assert s.query(ReadingLock).count() == 0 and s.query(ExtractionRow).count() == 1


def test_a_dead_workers_reservation_is_reconciled_when_its_lock_is_taken_over(store, tmp_path):
    """Worker A reserved OCR pages and died before settling them: the worker that takes its lock over marks the
    reservation abandoned (still counted against the budget, no longer pending)."""
    import hashlib
    with store.system() as s:
        s.add_all([Tenant(id=1, name="T"), Matter(id=1, tenant_id=1, name="U", commencement=T0.date(), announced=T0.date(), claims_due=T0.date())])
    rng = random.Random(4)
    data = render_pdf(DocContent([[("title", "LETTER OF AUTHORITY"), ("kv", "Financial creditor", "X"),
                                   ("kv", "Valid until", "31.03.2027")]]), rng)
    now = [T0]
    engine = Engine(store, load_pack(), T0.date(), tmp_path / "blobs", clock=lambda: now[0])
    upload = engine.ingest(1, 1, "a.pdf", data, "staff_upload", T0)
    with store.tenant(1) as s:
        s.add(ReadingLock(sha256=hashlib.sha256(data).hexdigest(), tenant_id=1, owner="worker-a", locked_at=T0))
        s.add(UsageRow(day=T0.date(), provider="ocr", units=3, state="reserved", reserved_by="worker-a"))
    now[0] = T0 + timedelta(seconds=LOCK_SECONDS + 1)
    engine.prefetch(1, upload)
    with store.tenant(1) as s:
        assert [(u.units, u.state) for u in s.query(UsageRow)] == [(3, "abandoned")]
        assert s.query(ReadingLock).count() == 0


def test_concurrent_workers_never_lease_the_same_job(store):
    import threading
    for i in range(40):
        jobs.enqueue(store, 1, "process_upload", f"k{i}", now=T0)
    leased, errors = [], []

    def work(name):
        try:
            while (job := jobs.lease(store, name, lease_seconds=600, now=T0)) is not None:
                leased.append(job.id)
        except Exception as exc:  # noqa: BLE001 - collected and asserted below
            errors.append(exc)
    threads = [threading.Thread(target=work, args=(f"w{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and len(leased) == 40 and len(set(leased)) == 40

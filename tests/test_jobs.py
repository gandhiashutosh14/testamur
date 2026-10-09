"""The queue and the spend gates: leases expire, failures back off then dead-letter, deferral is not failure, and a
tenant over budget pays for nothing."""
import random
from datetime import datetime, timedelta

import pytest

from testamur import jobs
from testamur.budget import BudgetExceeded
from testamur.engine import Engine
from testamur.pack import load_pack
from testamur.store import BusinessUnit, Store, Tenant, TenantBudget, UsageRow
from testamur.synth.render import DocContent, render_pdf, to_scan_pdf

T0 = datetime(2026, 10, 1, 9, 0)


@pytest.fixture()
def store(tmp_path):
    return Store(f"sqlite:///{tmp_path / 'q.db'}")


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
    assert jobs.fail(store, j.id, "boom", now=T0) == "queued"
    assert jobs.lease(store, "w", now=T0) is None                       # backing off
    j = jobs.lease(store, "w", now=T0 + timedelta(seconds=10))
    assert jobs.fail(store, j.id, "boom again", now=T0) == "dead"
    assert jobs.counts(store) == {"dead": 1}


def test_a_deferral_is_not_an_attempt(store):
    jobs.enqueue(store, 1, "process_upload", "a", max_attempts=1, now=T0)
    j = jobs.lease(store, "w", now=T0)
    jobs.defer(store, j.id, T0 + timedelta(days=1), "over budget")
    assert jobs.lease(store, "w", now=T0 + timedelta(hours=1)) is None
    j = jobs.lease(store, "w", now=T0 + timedelta(days=1))
    assert j is not None and j.attempts == 1                            # still its first real attempt


def test_a_tenant_over_its_ocr_budget_pays_for_nothing(store, tmp_path):
    with store.system() as s:
        s.add_all([Tenant(id=1, name="T"), BusinessUnit(id=1, tenant_id=1, name="U"),
                   TenantBudget(tenant_id=1, ocr_pages_per_day=0)])
    rng = random.Random(1)
    scan = to_scan_pdf(render_pdf(DocContent([[("title", "CERTIFICATE OF INSURANCE"), ("kv", "Insurer", "X")]]), rng), rng)
    engine = Engine(store, load_pack(), T0.date(), tmp_path / "blobs", clock=lambda: T0)
    uid = engine.ingest(1, 1, "scan.pdf", scan, "staff_upload", T0)
    with pytest.raises(BudgetExceeded) as exc:
        engine.prefetch(1, uid)
    assert exc.value.retry_at == datetime(2026, 10, 2)
    with store.tenant(1) as s:
        assert s.query(UsageRow).count() == 0                            # refused before any page was read
    born_digital = render_pdf(DocContent([[("title", "CERTIFICATE OF INSURANCE"), ("kv", "Insurer", "X"),
                                           ("kv", "Policy number", "P-1")]]), rng)
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

"""Tenant isolation is enforced by the session, not by remembering a filter, and by the database underneath it. These
tests try to break it."""
import random
from datetime import date, datetime

import pytest
from sqlalchemy import delete, insert, select, text, update
from sqlalchemy.exc import IntegrityError

from testamur.engine import Engine, ReadingElsewhere
from testamur.extract import Extractor
from testamur.pack import load_pack
from testamur.store import Claim, Creditor, Document, Matter, ReadingLock, Store, Tenant, TenantScopeError
from testamur.synth.render import DocContent, render_pdf

D = date(2026, 9, 1)


@pytest.fixture()
def store(db_url):
    st = Store(db_url)
    with st.system() as s:
        s.add_all([Tenant(id=1, name="A"), Tenant(id=2, name="B"),
                   Creditor(id=1, tenant_id=1, legal_name="Alpha Ltd"), Creditor(id=2, tenant_id=2, legal_name="Beta Ltd"),
                   Matter(id=1, tenant_id=1, name="Debtor One Ltd", commencement=D, announced=D, claims_due=D),
                   Matter(id=2, tenant_id=2, name="Debtor Two Ltd", commencement=D, announced=D, claims_due=D)])
    return st


def test_a_tenant_session_sees_only_its_own_rows(store):
    with store.tenant(1) as s:
        assert [x.legal_name for x in s.scalars(select(Creditor))] == ["Alpha Ltd"]
        assert s.get(Creditor, 2) is None                      # not even by primary key
    with store.tenant(2) as s:
        assert [x.legal_name for x in s.scalars(select(Creditor))] == ["Beta Ltd"]


def test_a_session_without_a_tenant_fails_closed(store):
    with store.unscoped() as s, pytest.raises(TenantScopeError):
        s.scalars(select(Creditor)).all()


def test_a_write_into_another_tenant_is_refused(store):
    with pytest.raises(TenantScopeError), store.tenant(1) as s:
        s.add(Creditor(id=3, tenant_id=2, legal_name="Planted Ltd"))
    with store.system() as s:
        assert s.get(Creditor, 3) is None


def test_rows_written_without_a_tenant_take_the_session_tenant(store):
    with store.tenant(2) as s:
        d = Document(sha256="ab" * 32)
        s.add(d)
        s.flush()
        assert d.tenant_id == 2


def test_a_job_with_no_tenant_cannot_open_a_session(store):
    with pytest.raises(TenantScopeError), store.tenant(None):
        pass


def test_bulk_update_and_delete_reach_only_the_sessions_tenant(store):
    """A bulk statement never passes through the flush checks; the session's filter must reach it anyway."""
    with store.tenant(1) as s:
        assert s.execute(update(Creditor).values(legal_name="Renamed Ltd")).rowcount == 1
        assert s.execute(delete(Creditor).where(Creditor.id == 2)).rowcount == 0
    with store.system() as s:
        assert s.get(Creditor, 2).legal_name == "Beta Ltd"
        assert s.get(Creditor, 1).legal_name == "Renamed Ltd"


@pytest.mark.parametrize("statement", [
    lambda: text("UPDATE creditors SET legal_name = 'x'"),                          # textual SQL
    lambda: select(Creditor.__table__),                                             # Core: no mapped entity
    lambda: insert(Creditor).values(id=9, tenant_id=2, legal_name="Planted Ltd"),   # bulk INSERT
    lambda: update(Creditor).values(tenant_id=2),                                    # moving rows to a tenant
], ids=["text", "core", "insert", "moves-tenant"])
def test_statements_the_tenant_filter_cannot_reach_are_refused(store, statement):
    with pytest.raises(TenantScopeError), store.tenant(1) as s:
        s.execute(statement())


def test_a_bulk_update_by_primary_key_is_refused(store):
    with pytest.raises(TenantScopeError), store.tenant(1) as s:
        s.execute(update(Creditor), [{"id": 2, "legal_name": "Taken Ltd"}])
    with store.system() as s:
        assert s.get(Creditor, 2).legal_name == "Beta Ltd"


def test_deleting_another_tenants_row_is_refused(store):
    with store.system() as s:
        theirs = s.get(Creditor, 2)
    with pytest.raises(TenantScopeError), store.tenant(1) as s:
        s.add(theirs)                                      # a detached row of tenant 2, brought into tenant 1
        s.delete(theirs)
    with store.system() as s:
        assert s.get(Creditor, 2) is not None


def test_a_reference_into_another_tenant_is_refused_by_the_database(store):
    """A claim of tenant 1 pointing at tenant 2's matter: the foreign key is on (tenant_id, id)."""
    with pytest.raises(IntegrityError), store.tenant(1) as s:
        s.add(Claim(id=1, matter_id=2, creditor_id=1, tier="high", claim_type="secured", facility_type="term_loan",
                    book_balance=1, sanctioned=1))
    with pytest.raises(IntegrityError), store.tenant(1) as s:          # a trustee that is another tenant's creditor
        s.add(Creditor(id=5, legal_name="Holder Ltd", trustee_id=2))


def _letter(seed: int) -> bytes:
    return render_pdf(DocContent([[("title", "LETTER OF AUTHORITY"), ("kv", "Financial creditor", "Alpha Ltd"),
                                   ("kv", "Valid until", "31.03.2027")]]), random.Random(seed))


def test_finishing_a_read_releases_only_this_workers_lock(store, tmp_path):
    """Tenant 1 reads a file that tenant 2 is reading at the same moment: tenant 2's lock must survive."""
    data, t0 = _letter(1), datetime(2026, 9, 2, 9, 0)
    engine = Engine(store, load_pack(), D, tmp_path / "blobs", clock=lambda: t0)
    mine = engine.ingest(1, 1, "a.pdf", data, "staff_upload", t0)
    engine.ingest(2, 2, "b.pdf", data, "staff_upload", t0)
    import hashlib
    sha = hashlib.sha256(data).hexdigest()
    with store.tenant(2) as s:
        s.add(ReadingLock(sha256=sha, tenant_id=2, owner="worker-b", locked_at=t0))
    engine.prefetch(1, mine)
    with store.tenant(2) as s:
        assert s.get(ReadingLock, {"sha256": sha, "tenant_id": 2}).owner == "worker-b"


def test_a_worker_whose_lock_was_taken_over_stops_and_releases_nothing(store, tmp_path):
    """A slow worker comes back after another took its stale lock over: it stops paying for pages and leaves the new
    owner's lock alone."""
    from testamur.synth.render import to_scan_pdf
    rng = random.Random(3)
    scan, t0 = to_scan_pdf(_letter(2), rng), datetime(2026, 9, 2, 9, 0)
    import hashlib
    sha = hashlib.sha256(scan).hexdigest()

    class TakenOver(Extractor):
        def extract(self, data, mime, before_ocr=None, on_page=None):
            with store.tenant(1) as s:                     # meanwhile, another worker took the lock over
                s.execute(update(ReadingLock).where(ReadingLock.sha256 == sha).values(owner="worker-b"))
            on_page()
            raise AssertionError("the page must not be read: the lock is gone")

    engine = Engine(store, load_pack(), D, tmp_path / "blobs", extractor=TakenOver(), clock=lambda: t0)
    upload = engine.ingest(1, 1, "scan.pdf", scan, "staff_upload", t0)
    with pytest.raises(ReadingElsewhere):
        engine.prefetch(1, upload)
    with store.tenant(1) as s:
        assert s.get(ReadingLock, {"sha256": sha, "tenant_id": 1}).owner == "worker-b"

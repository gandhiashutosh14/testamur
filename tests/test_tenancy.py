"""Tenant isolation is enforced by the session, not by remembering a filter. These tests try to break it."""
import pytest
from sqlalchemy import select

from testamur.store import Document, Store, Supplier, Tenant, TenantScopeError


@pytest.fixture()
def store(db_url):
    st = Store(db_url)
    with st.system() as s:
        s.add_all([Tenant(id=1, name="A"), Tenant(id=2, name="B"),
                   Supplier(id=1, tenant_id=1, legal_name="Alpha Ltd"), Supplier(id=2, tenant_id=2, legal_name="Beta Ltd")])
    return st


def test_a_tenant_session_sees_only_its_own_rows(store):
    with store.tenant(1) as s:
        assert [x.legal_name for x in s.scalars(select(Supplier))] == ["Alpha Ltd"]
        assert s.get(Supplier, 2) is None                      # not even by primary key
    with store.tenant(2) as s:
        assert [x.legal_name for x in s.scalars(select(Supplier))] == ["Beta Ltd"]


def test_a_session_without_a_tenant_fails_closed(store):
    with store.unscoped() as s, pytest.raises(TenantScopeError):
        s.scalars(select(Supplier)).all()


def test_a_write_into_another_tenant_is_refused(store):
    with pytest.raises(TenantScopeError), store.tenant(1) as s:
        s.add(Supplier(id=3, tenant_id=2, legal_name="Planted Ltd"))
    with store.system() as s:
        assert s.get(Supplier, 3) is None


def test_rows_written_without_a_tenant_take_the_session_tenant(store):
    with store.tenant(2) as s:
        d = Document(sha256="ab" * 32)
        s.add(d)
        s.flush()
        assert d.tenant_id == 2


def test_a_job_with_no_tenant_cannot_open_a_session(store):
    with pytest.raises(TenantScopeError), store.tenant(None):
        pass
